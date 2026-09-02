"""
Phone (or any browser) as a remote pickup.

Runs a small web server on the LAN, on a stable port so the URL does not
change between recordings. Open the URL on a phone, tap Start, and the
browser streams microphone audio back -- either as raw Int16 PCM over a
hand-rolled WebSocket, or over WebRTC (`aiortc`), which is steadier on a
marginal Wi-Fi link. The page routes the mic through a gain node with a
slider, and the server adds its own makeup AGC on top, because a watch tick
through a phone mic is a very quiet signal. Either way the audio lands in a
ring buffer that presents the same surface as audio.Recorder.

Browsers only expose `navigator.mediaDevices` in a "secure context" -- HTTPS
or localhost -- so the server generates a throwaway self-signed certificate
and serves HTTPS when `cryptography` is available. The phone shows a one-time
"not private" warning that you tap through. Without `cryptography` it falls
back to HTTP, where phone browsers will refuse microphone access.

The server binds only to this machine's LAN address (never 0.0.0.0), and the
watch list, state and command endpoints all require a random per-session token
that is embedded in the page. It only ever receives audio -- it never plays
anything back or runs code from the page.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import queue
import secrets
import socket
import ssl
import struct
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

import numpy as np

try:
    import aiortc  # noqa: F401
    HAVE_AIORTC = True
except Exception:
    HAVE_AIORTC = False

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    HAVE_CRYPTO = True
except Exception:
    HAVE_CRYPTO = False

_WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _content_length(headers, cap: int) -> int:
    """Parse Content-Length defensively and clamp it to `cap` bytes."""
    try:
        n = int(headers.get("Content-Length", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, cap))


def _self_signed(ip: str):
    """Return (cert_pem_bytes, key_pem_bytes) for a throwaway HTTPS cert, or None."""
    if not HAVE_CRYPTO:
        return None
    import datetime as _dt
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "WatchGrapher remote")])
    san = [x509.DNSName("localhost")]
    try:
        import ipaddress
        san.append(x509.IPAddress(ipaddress.ip_address(ip)))
    except Exception:
        pass
    now = _dt.datetime.utcnow()
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _dt.timedelta(days=1))
            .not_valid_after(now + _dt.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.TraditionalOpenSSL,
                              serialization.NoEncryption()))


def lan_ip() -> str:
    """Best guess at this machine's LAN address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _resample(x: np.ndarray, src_sr: float, dst_sr: int) -> np.ndarray:
    if abs(src_sr - dst_sr) < 1.0 or x.size == 0:
        return x.astype(np.float32)
    n_out = int(round(x.size * dst_sr / src_sr))
    if n_out < 1:
        return np.zeros(0, dtype=np.float32)
    xp = np.linspace(0.0, 1.0, x.size, endpoint=False)
    fp = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(fp, xp, x).astype(np.float32)


class NetworkRecorder:
    """A ring-buffer capture fed by browser audio. Mirrors audio.Recorder."""

    def __init__(self, samplerate: int = 48000, buffer_seconds: float = 60.0,
                 port: int = 0):
        self.samplerate = int(samplerate)
        self.buffer_seconds = float(buffer_seconds)
        self.n = int(self.buffer_seconds * self.samplerate)
        self._buf = np.zeros(self.n, dtype=np.float32)
        self._write = 0
        self._filled = 0
        self._lock = threading.Lock()
        self.peak = 0.0
        self.frames = 0
        self.overflows = 0
        self.clips = 0
        self.agc_enabled = True
        self.gain = 1.0                # server-side makeup gain (auto)
        self._agc_target = 0.4
        self.opened_note = ""
        self._want_port = int(port)
        self._srv: Optional[ThreadingHTTPServer] = None
        self._srv_thread: Optional[threading.Thread] = None
        self._closing = threading.Event()
        self.port = 0
        self.url = ""
        self._last_rx = 0.0
        self._rtc_pcs = set()
        self._rtc_loop = None
        self._rtc_thread: Optional[threading.Thread] = None
        self._certfile = None
        # Remote control: the app publishes JSON snapshots here and drains the
        # command queue on its GUI thread.
        self.token = secrets.token_hex(16)
        self.state_json = "{}"
        self.watches_json = "[]"
        self.cmd_q: "queue.Queue" = queue.Queue(maxsize=64)

    def _wipe_certfile(self):
        if self._certfile:
            try:
                import os
                os.remove(self._certfile)
            except OSError:
                pass
            self._certfile = None

    def drain_commands(self):
        out = []
        try:
            while True:
                out.append(self.cmd_q.get_nowait())
        except queue.Empty:
            pass
        return out

    # -- lifecycle --------------------------------------------------------------
    def start(self):
        if self._srv is not None:
            return                      # already serving -- reuse it
        self._closing.clear()
        rec = self

        class _Server(ThreadingHTTPServer):
            # Windows lets two sockets share a port with SO_REUSEADDR, which would
            # hide a real conflict from the "try the next port" loop below.
            allow_reuse_address = False
            daemon_threads = True

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):        # keep the console quiet
                pass

            def _authed(self):
                tok = self.headers.get("X-WG-Token", "")
                if not tok:
                    q = urlparse(self.path).query
                    tok = parse_qs(q).get("t", [""])[0]
                return secrets.compare_digest(tok, rec.token)

            def _send(self, code, body=b"", ctype="text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):
                path = urlparse(self.path).path
                if path in ("/", "/index.html"):
                    self._send(200, PAGE.replace("__TOKEN__", rec.token).encode("utf-8"),
                               "text/html; charset=utf-8")
                elif path == "/ws":
                    rec._serve_ws(self)
                elif path == "/rtc-available":
                    self._send(200, json.dumps({"aiortc": HAVE_AIORTC}).encode(),
                               "application/json")
                elif path in ("/api/state", "/api/watches"):
                    if not self._authed():
                        self._send(403, b'{"ok":false,"err":"bad token"}', "application/json")
                        return
                    body = (rec.state_json if path == "/api/state"
                            else rec.watches_json).encode()
                    self._send(200, body, "application/json")
                else:
                    self._send(404)

            def do_POST(self):
                if self.path == "/rtc-offer":
                    if not self._authed():
                        self._send(403, b'{"ok":false,"err":"bad token"}', "application/json")
                        return
                    n = _content_length(self.headers, 65536)
                    try:
                        offer = json.loads(self.rfile.read(n) or b"{}")
                        answer = rec._rtc_answer(offer)
                        self._send(200, json.dumps(answer).encode(), "application/json")
                    except Exception as e:
                        self._send(500, str(e).encode())
                elif self.path == "/api/cmd":
                    if not self._authed():
                        self._send(403, b'{"ok":false,"err":"bad token"}', "application/json")
                        return
                    n = _content_length(self.headers, 4096)
                    try:
                        obj = json.loads(self.rfile.read(n) or b"{}")
                        rec.cmd_q.put_nowait(obj)
                    except (ValueError, queue.Full):
                        pass
                    self._send(200, b'{"ok":true}', "application/json")
                else:
                    self._send(404)

        ip = lan_ip()
        # Bind only to the LAN address, not 0.0.0.0 -- the pickup has no business
        # being reachable over a VPN or a public-Wi-Fi interface.
        bind = ip
        # Prefer a stable port so the URL does not change between recordings.
        candidates = ([self._want_port + i for i in range(20)] if self._want_port
                      else []) + [0]
        self._srv = None
        for cand in candidates:
            try:
                self._srv = _Server((bind, cand), Handler)
                break
            except OSError:
                continue
        if self._srv is None:
            self._srv = _Server((bind, 0), Handler)
        self.port = self._srv.server_address[1]

        self.secure = False
        pair = _self_signed(ip)
        if pair is not None:
            try:
                cert_pem, key_pem = pair
                cf = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
                cf.write(cert_pem + key_pem)
                cf.close()
                self._certfile = cf.name
                try:
                    import os as _os
                    _os.chmod(self._certfile, 0o600)     # private key -- owner only
                except OSError:
                    pass
                # Belt and braces: drop the key file even if we exit uncleanly.
                import atexit
                atexit.register(self._wipe_certfile)
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(self._certfile)
                self._srv.socket = ctx.wrap_socket(self._srv.socket, server_side=True)
                self.secure = True
            except Exception:
                self.secure = False

        scheme = "https" if self.secure else "http"
        self.url = f"{scheme}://{ip}:{self.port}"
        if self.secure:
            self.opened_note = (
                f"Phone pickup ready at {self.url} -- open it on a device on the same "
                f"network. The phone will warn the certificate is not trusted: that is "
                f"expected for a local self-signed cert, tap through it, then tap Start.")
        else:
            self.opened_note = (
                f"Phone pickup at {self.url} -- WARNING: without the 'cryptography' "
                f"package the server can only use plain HTTP, and phone browsers block "
                f"microphone access on HTTP. Install it (pip install cryptography) and "
                f"restart for this to work.")
        self._srv_thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._srv_thread.start()

    def stop(self):
        self._closing.set()
        srv = self._srv
        self._srv = None
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
        t = self._srv_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._srv_thread = None

        loop = self._rtc_loop
        if loop is not None and not loop.is_closed():
            async def _closeall():
                for pc in list(self._rtc_pcs):
                    try:
                        await pc.close()
                    except Exception:
                        pass
                self._rtc_pcs.clear()
            try:
                asyncio.run_coroutine_threadsafe(_closeall(), loop).result(timeout=3.0)
            except Exception:
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
            rt = self._rtc_thread
            if rt is not None and rt.is_alive():
                rt.join(timeout=2.0)
            try:
                loop.close()
            except Exception:
                pass
        self._rtc_loop = None
        self._rtc_thread = None
        self._rtc_pcs = set()

        self._wipe_certfile()
        self._last_rx = 0.0
        self.port = 0
        self.url = ""

    # -- Recorder surface -----------------------------------------------------
    @property
    def running(self):
        return self._srv is not None and not self._closing.is_set()

    @property
    def connected(self):
        """True while audio has arrived in the last few seconds."""
        return (time.time() - self._last_rx) < 3.0

    @property
    def is_recording(self):
        return False

    def stop_recording(self):
        return ""

    def start_recording(self, path):
        pass

    def clear(self):
        with self._lock:
            self._buf[:] = 0.0
            self._write = 0
            self._filled = 0

    @property
    def seconds_buffered(self):
        return self._filled / self.samplerate

    def read(self, seconds: float) -> np.ndarray:
        k = min(self.n, int(seconds * self.samplerate))
        with self._lock:
            if self._filled < k:
                k = self._filled
            if k == 0:
                return np.zeros(0, dtype=np.float32)
            start = (self._write - k) % self.n
            if start + k <= self.n:
                return self._buf[start:start + k].copy()
            return np.concatenate([self._buf[start:], self._buf[: (start + k) % self.n]])

    # -- feed ---------------------------------------------------------------
    def feed(self, pcm: np.ndarray, src_sr: float):
        data = _resample(np.asarray(pcm, dtype=np.float32), src_sr, self.samplerate)
        if data.size == 0:
            return
        self._last_rx = time.time()
        raw_p = float(np.max(np.abs(data)))
        if raw_p >= 0.999:
            self.clips += 1

        # A phone mic on a watch tick is a very quiet signal. The page boosts it
        # first (its gain slider); this adds an automatic makeup gain toward a
        # comfortable level for the DSP -- same idea as the USB Recorder's AGC.
        if self.agc_enabled:
            pg = float(np.max(np.abs(data))) or 1e-4
            if pg >= 0.98:
                self.gain = max(0.5, self.gain * 0.8)
            else:
                want = np.clip(self._agc_target / (pg * self.gain), 0.2, 5.0)
                self.gain = float(np.clip(self.gain * (0.98 + 0.02 * want), 0.5, 40.0))
            data = data * self.gain
        else:
            self.gain = 1.0
        np.clip(data, -1.0, 1.0, out=data)

        p = float(np.max(np.abs(data)))
        self.peak = max(self.peak * 0.92, p)
        self.frames += data.size
        with self._lock:
            k = data.size
            if k >= self.n:
                self._buf[:] = data[-self.n:]
                self._write = 0
                self._filled = self.n
                return
            end = self._write + k
            if end <= self.n:
                self._buf[self._write:end] = data
            else:
                split = self.n - self._write
                self._buf[self._write:] = data[:split]
                self._buf[: end - self.n] = data[split:]
            self._write = end % self.n
            self._filled = min(self.n, self._filled + k)

    # -- WebSocket (hand-rolled, receive-only for audio) -------------------------
    def _serve_ws(self, handler):
        upgrade = handler.headers.get("Upgrade", "").lower()
        version = handler.headers.get("Sec-WebSocket-Version", "")
        key = handler.headers.get("Sec-WebSocket-Key", "")
        if "websocket" not in upgrade or version != "13" or not key:
            handler._send(400)
            return
        accept = base64.b64encode(
            hashlib.sha1(key.encode() + _WS_GUID).digest()).decode()
        handler.send_response(101)
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", accept)
        handler.end_headers()
        conn = handler.connection
        try:
            conn.settimeout(1.0)
        except OSError:
            pass
        src_sr = float(self.samplerate)
        last_data = time.time()
        try:
            while not self._closing.is_set():
                frame = _ws_read_frame(conn, self._closing)
                if frame is None:
                    break                   # client disconnected
                if frame is _IDLE:
                    if time.time() - last_data > 30.0:
                        break               # silent client -- free the thread
                    continue
                last_data = time.time()
                opcode, payload = frame
                if opcode == 0x8:            # close
                    break
                if opcode == 0x9:            # ping -> pong
                    conn.sendall(_ws_frame(0xA, payload))
                    continue
                if opcode == 0x1:            # text control
                    try:
                        msg = json.loads(payload.decode("utf-8"))
                        if "sr" in msg:
                            src_sr = float(msg["sr"])
                    except Exception:
                        pass
                    continue
                if opcode == 0x2:            # binary Int16LE PCM
                    pcm = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0
                    self.feed(pcm, src_sr)
        except OSError:
            pass

    # -- WebRTC (optional) ----------------------------------------------------
    def _rtc_answer(self, offer: dict) -> dict:
        if not HAVE_AIORTC:
            raise RuntimeError("aiortc not installed")
        import asyncio
        from aiortc import RTCPeerConnection, RTCSessionDescription
        from aiortc.mediastreams import MediaStreamError

        if self._rtc_loop is None:
            self._rtc_loop = asyncio.new_event_loop()
            self._rtc_thread = threading.Thread(
                target=self._rtc_loop.run_forever, daemon=True)
            self._rtc_thread.start()
        loop = self._rtc_loop

        async def negotiate():
            pc = RTCPeerConnection()
            self._rtc_pcs.add(pc)

            @pc.on("connectionstatechange")
            async def _state():
                if pc.connectionState in ("failed", "closed"):
                    self._rtc_pcs.discard(pc)
                    await pc.close()

            @pc.on("track")
            def _on_track(track):
                if track.kind != "audio":
                    return

                async def pump():
                    while True:
                        try:
                            frame = await track.recv()
                        except MediaStreamError:
                            break
                        arr = frame.to_ndarray()
                        if arr.ndim > 1:
                            arr = arr.mean(axis=0)
                        if np.issubdtype(arr.dtype, np.integer):
                            arr = arr.astype(np.float32) / 32768.0
                        else:
                            arr = arr.astype(np.float32)
                        self.feed(arr, frame.sample_rate)

                loop.create_task(pump())

            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=offer["sdp"], type=offer["type"]))
            ans = await pc.createAnswer()
            await pc.setLocalDescription(ans)
            return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

        fut = asyncio.run_coroutine_threadsafe(negotiate(), loop)
        return fut.result(timeout=10)


# --------------------------------------------------------------------------
# minimal WebSocket frame codec
# --------------------------------------------------------------------------

_IDLE = object()          # recv timed out with nothing pending (not a disconnect)


def _recv_exact(conn, n, stop=None):
    out = b""
    while len(out) < n:
        try:
            chunk = conn.recv(n - len(out))
        except socket.timeout:
            if stop is not None and stop.is_set():
                return None
            if not out:
                return _IDLE          # between frames -- caller can idle-time-out
            continue                  # mid-frame -- keep waiting for the rest
        except OSError:
            return None
        if not chunk:
            return None
        out += chunk
    return out


def _ws_read_frame(conn, stop=None):
    hdr = _recv_exact(conn, 2, stop)
    if hdr is None or hdr is _IDLE:
        return hdr
    b0, b1 = hdr[0], hdr[1]
    opcode = b0 & 0x0F
    masked = b1 & 0x80
    length = b1 & 0x7F
    if length == 126:
        ext = _recv_exact(conn, 2, stop)
        if ext is None:
            return None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = _recv_exact(conn, 8, stop)
        if ext is None:
            return None
        length = struct.unpack(">Q", ext)[0]
    mask = _recv_exact(conn, 4, stop) if masked else b"\x00\x00\x00\x00"
    if mask is None:
        return None
    payload = _recv_exact(conn, length, stop) if length else b""
    if payload is None:
        return None
    if masked and payload:
        pb = bytearray(payload)
        for i in range(len(pb)):
            pb[i] ^= mask[i & 3]
        payload = bytes(pb)
    return opcode, payload


def _ws_frame(opcode, payload=b""):
    out = bytearray([0x80 | (opcode & 0x0F)])
    n = len(payload)
    if n < 126:
        out.append(n)
    elif n < 65536:
        out.append(126)
        out += struct.pack(">H", n)
    else:
        out.append(127)
        out += struct.pack(">Q", n)
    out += payload
    return bytes(out)


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WatchGrapher remote</title>
<style>
  :root{color-scheme:dark}
  body{font-family:-apple-system,Segoe UI,sans-serif;background:#12161c;color:#e8eef7;
       margin:0;padding:20px;text-align:center}
  h1{font-size:17px;font-weight:600;margin:0 0 4px}
  button{font-size:18px;padding:15px 26px;border-radius:10px;border:0;margin:6px;
         background:#4da3ff;color:#08101c;font-weight:700}
  button.stop{background:#ff5d5d;color:#fff}
  button:disabled{opacity:.4}
  button.sec{background:#2a323e;color:#e8eef7;font-size:15px;padding:11px 18px}
  select{font-size:16px;padding:9px;border-radius:8px;background:#1a1f27;color:#e8eef7;
         border:1px solid #2a323e;max-width:92%}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;max-width:360px;margin:14px auto}
  .tile{background:#1a1f27;border:1px solid #2a323e;border-radius:10px;padding:10px 6px}
  .tile .k{font-size:11px;color:#8a94a4;letter-spacing:.05em}
  .tile .v{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}
  #meter{height:14px;background:#1a1f27;border-radius:7px;margin:12px auto;max-width:340px}
  #bar{height:100%;width:0;background:#57d38c;border-radius:7px}
  #clip{color:#ff5d5d;font-size:13px;min-height:16px}
  #status{color:#8a94a4;font-size:13px;min-height:18px}
  #desk{color:#7fb2ff;font-size:13px;min-height:18px}
  .row{margin:10px 0}
  details{max-width:360px;margin:10px auto;text-align:left;color:#b6bfcc;font-size:14px}
  summary{cursor:pointer;color:#8a94a4}
  input[type=range]{width:260px}
</style></head><body>
<h1>WatchGrapher remote</h1>
<p id="desk">connecting...</p>

<div class="row"><select id="watch"><option value="">(no watch)</option></select></div>
<div class="row"><select id="dur">
  <option value="0">Open-ended</option>
  <option value="20">Timed 20 s</option>
  <option value="30" selected>Timed 30 s</option>
  <option value="60">Timed 60 s</option>
  <option value="120">Timed 2 min</option>
  <option value="300">Timed 5 min</option>
</select></div>

<div class="grid">
  <div class="tile"><div class="k">RATE s/d</div><div class="v" id="t_rate">--</div></div>
  <div class="tile"><div class="k">AMPLITUDE</div><div class="v" id="t_amp">--</div></div>
  <div class="tile"><div class="k">BEAT ERROR ms</div><div class="v" id="t_be">--</div></div>
  <div class="tile"><div class="k">BEAT RATE</div><div class="v" id="t_bph">--</div></div>
</div>

<div id="prog" style="max-width:340px;margin:6px auto;color:#8a94a4;font-size:13px;
     min-height:16px"></div>
<div id="meter"><div id="bar"></div></div>
<div id="clip"></div>
<p id="status">idle</p>
<video id="nosleep" playsinline muted loop style="position:fixed;width:1px;height:1px;
  opacity:0;pointer-events:none"></video>

<button id="go">Start test</button>
<button id="stop" class="stop" disabled>Stop</button>
<div class="row"><button id="save" class="sec" disabled>Save run to watch</button></div>

<div id="finished" style="display:none;max-width:360px;margin:12px auto;padding:14px;
     background:#1a1f27;border:1px solid #2a323e;border-radius:12px">
  <div style="font-weight:700;margin-bottom:6px">Run finished</div>
  <pre id="fsum" style="white-space:pre-wrap;text-align:left;color:#c8d0dc;
       font-family:inherit;font-size:14px;margin:0 0 10px"></pre>
  <button id="fsave" class="sec">Save to watch</button>
  <button id="fdiscard" class="sec">Discard</button>
</div>

<details>
  <summary>Audio settings</summary>
  <div class="row">
    <label><input type="radio" name="mode" value="pcm" checked> PCM (WebSocket)</label>
    &nbsp; <label><input type="radio" name="mode" value="rtc" id="rtcopt"> WebRTC</label>
  </div>
  <div class="row">Boost <span id="gval">6&times;</span><br>
    <input type="range" id="gain" min="1" max="20" step="1" value="6"></div>
  <div class="row"><label><input type="checkbox" id="hwagc">
    Let the phone auto-level (may pump)</label></div>
  <p style="color:#5a6472;font-size:13px">Press the phone's mic port against the case
  back. Turn Boost up until the meter sits high but CLIP stays quiet.</p>
</details>

<script>
const TOKEN="__TOKEN__";
let ac,node,src,gainNode,dest,stream,ws,pc,wake,running=false,clips=0,retry=0,poll=null,
    runSeen=false;
const $=id=>document.getElementById(id);
const AUTH={headers:{'X-WG-Token':TOKEN}};
const get=p=>fetch(p,AUTH);
const status=$("status"),desk=$("desk"),bar=$("bar"),clip=$("clip"),prog=$("prog"),
      gainEl=$("gain"),gval=$("gval"),goBtn=$("go"),stopBtn=$("stop"),
      saveBtn=$("save"),watchEl=$("watch"),durEl=$("dur"),nosleep=$("nosleep"),
      finished=$("finished"),fsum=$("fsum"),fsave=$("fsave"),fdiscard=$("fdiscard");

// keep the screen awake: Wake Lock where supported, plus a playing muted video
// fed from a canvas stream (which holds the screen on iOS Safari too).
async function keepAwake(){
  try{ if('wakeLock' in navigator){ wake=await navigator.wakeLock.request('screen'); } }catch(e){}
  try{
    if(!nosleep.srcObject){
      const c=document.createElement('canvas'); c.width=c.height=2;
      nosleep.srcObject=c.captureStream(1);
    }
    await nosleep.play();
  }catch(e){}
}
function releaseAwake(){
  try{ wake&&wake.release(); }catch(e){} wake=null;
  try{ nosleep.pause();
    if(nosleep.srcObject){ nosleep.srcObject.getTracks().forEach(t=>t.stop());
      nosleep.srcObject=null; } }catch(e){}
}
fetch('/rtc-available').then(r=>r.json()).then(j=>{
  if(!j.aiortc){const o=$("rtcopt");o.disabled=true;o.parentNode.style.opacity=.4;
    o.parentNode.title='Start the app with aiortc installed for WebRTC';}
});
gainEl.oninput=()=>{ gval.innerHTML=gainEl.value+'&times;';
  if(gainNode) gainNode.gain.value=parseFloat(gainEl.value); };
function mode(){return document.querySelector('input[name=mode]:checked').value;}
function cmd(name,extra){
  return fetch('/api/cmd',{method:'POST',
    headers:{'Content-Type':'application/json','X-WG-Token':TOKEN},
    body:JSON.stringify(Object.assign({cmd:name},extra||{}))}).catch(()=>{});
}
function meter(pk){
  bar.style.width=Math.min(100,pk*120)+'%';
  if(pk>=0.99){ clips++; clip.textContent='CLIP -- turn Boost down'; }
  else if(clips>0 && pk<0.6){ clips=Math.max(0,clips-1); if(clips===0) clip.textContent=''; }
}
document.addEventListener('visibilitychange',()=>{
  if(running && document.visibilityState==='visible') keepAwake();
});

async function loadWatches(sel){
  try{
    const list=await (await get('/api/watches')).json();
    const keep=sel||watchEl.value;
    watchEl.innerHTML='<option value="">(no watch)</option>'+
      list.map(w=>'<option value="'+w.id+'">'+w.label.replace(/</g,'&lt;')+'</option>').join('');
    if(keep) watchEl.value=keep;
  }catch(e){}
}
loadWatches();
watchEl.onchange=()=>cmd('select',{id:watchEl.value});

function fmt(v,d){ return (v===null||v===undefined)?'--':Number(v).toFixed(d); }
async function refresh(){
  let s; try{ s=await (await get('/api/state')).json(); }catch(e){ return; }
  if(!s.device_is_net)
    desk.textContent='Desktop input is not set to the phone pickup -- choose it there.';
  else if(s.listening)
    desk.textContent='desktop listening'+(s.watch?' -- '+s.watch:'');
  else
    desk.textContent='desktop idle'+(s.watch?' -- watch: '+s.watch:'');
  $("t_rate").textContent=(s.rate>0?'+':'')+fmt(s.rate,1);
  $("t_amp").textContent=fmt(s.amplitude,0);
  $("t_be").textContent=fmt(s.beat_error,2);
  $("t_bph").textContent=s.bph||'--';
  saveBtn.disabled=!(running && s.have_reading && watchEl.value);
  if(s.last_save) status.textContent=s.last_save;

  // progress / finished handling for the phone side
  function mmss(x){ x=Math.max(0,Math.round(x)); return Math.floor(x/60)+':'+('0'+(x%60)).slice(-2); }
  if(running && s.listening){
    runSeen=true;
    if(s.settling) prog.textContent='settling before the timed run...';
    else if(s.run_len>0){
      const e=s.run_elapsed||0;
      prog.textContent='timed run  '+mmss(e)+' / '+mmss(s.run_len);
      bar.style.width='';   // meter still shows audio level; progress is text
    } else prog.textContent='open-ended -- tap Stop when steady  ('+mmss(s.elapsed||0)+')';
  } else if(running && runSeen && !s.pending){
    prog.textContent='';    // run ended on the desktop; wait for the summary
  }
  if(running && runSeen && !s.listening){
    // the desktop run has ended (timed out or was stopped) -- tidy up locally
    finishLocally();
  }

  if(s.pending){
    finished.style.display='block';
    fsum.textContent=s.pending.summary||'';
    fsave.disabled=!(s.pending.have && watchEl.value);
    fsave.style.opacity=fsave.disabled?.4:1;
  } else {
    finished.style.display='none';
  }
}
fsave.onclick=async()=>{ fsave.disabled=true; await cmd('save_pending'); setTimeout(refresh,400); };
fdiscard.onclick=async()=>{ await cmd('discard'); finished.style.display='none';
  setTimeout(refresh,400); };

async function start(){
  stopStreams();
  if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){
    status.innerHTML='This browser needs the page over <b>https</b> for mic access. '+
      (location.protocol==='https:'?'Try Chrome or Safari.':
       'Reload using <b>https://'+location.host+'</b>.');
    return;
  }
  const agc=$("hwagc").checked;
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,
      noiseSuppression:false,autoGainControl:agc}});
  }catch(e){status.textContent='mic denied: '+e;return;}
  running=true; clips=0; retry=0; runSeen=false;
  goBtn.disabled=true; stopBtn.disabled=false;
  finished.style.display='none'; status.textContent='starting...';
  keepAwake();
  ac=new (window.AudioContext||window.webkitAudioContext)();
  try{ await ac.resume(); }catch(e){}
  src=ac.createMediaStreamSource(stream);
  gainNode=ac.createGain(); gainNode.gain.value=parseFloat(gainEl.value);
  src.connect(gainNode);
  if(mode()==='rtc'){ await startRtc(); } else { startPcm(); }
  await cmd('select',{id:watchEl.value});
  await cmd('start',{duration:parseInt(durEl.value,10)||0});
  if(!poll) poll=setInterval(refresh,700);
  refresh();
}

// the desktop run has ended; stop the local mic but keep polling for the summary
function finishLocally(){
  if(!running) return;
  running=false;
  goBtn.disabled=false; stopBtn.disabled=true;
  bar.style.width=0; clip.textContent=''; prog.textContent='';
  status.textContent='run finished';
  releaseAwake();
  stopStreams();
  if(poll){ clearInterval(poll); poll=null; }
}

function startPcm(){
  node=ac.createScriptProcessor(2048,1,1);
  node.onaudioprocess=e=>{
    const f=e.inputBuffer.getChannelData(0);
    let pk=0; const buf=new Int16Array(f.length);
    for(let i=0;i<f.length;i++){ const s=Math.max(-1,Math.min(1,f[i]));
      buf[i]=s<0?s*32768:s*32767; if(Math.abs(s)>pk)pk=Math.abs(s); }
    meter(pk);
    if(ws && ws.readyState===1) ws.send(buf.buffer);
  };
  gainNode.connect(node); node.connect(ac.destination);
  connectWs();
}
function connectWs(){
  if(!running) return;
  try{ ws && ws.close(); }catch(e){}
  ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');
  ws.binaryType='arraybuffer';
  ws.onopen=()=>{ retry=0; ws.send(JSON.stringify({sr:ac.sampleRate}));
    status.textContent='streaming (PCM)'; };
  ws.onclose=ws.onerror=()=>{
    if(!running) return;
    retry++;
    if(retry>20){ status.textContent='lost the connection -- tap Start again'; stop(); return; }
    status.textContent='reconnecting... ('+retry+')';
    setTimeout(connectWs, Math.min(1000*retry,5000));
  };
}
async function startRtc(){
  dest=ac.createMediaStreamDestination();
  gainNode.connect(dest);
  pc=new RTCPeerConnection();
  dest.stream.getAudioTracks().forEach(t=>pc.addTrack(t,dest.stream));
  pc.onconnectionstatechange=()=>{
    if(running && (pc.connectionState==='failed'||pc.connectionState==='disconnected')){
      status.textContent='WebRTC connection lost -- tap Start again'; stop(); }
  };
  const offer=await pc.createOffer({offerToReceiveAudio:false});
  await pc.setLocalDescription(offer);
  await new Promise(res=>{ if(pc.iceGatheringState==='complete')res();
    else pc.onicegatheringstatechange=()=>{ if(pc.iceGatheringState==='complete')res(); }; });
  let r;
  try{ r=await fetch('/rtc-offer',{method:'POST',
    headers:{'Content-Type':'application/json','X-WG-Token':TOKEN},
    body:JSON.stringify(pc.localDescription)}); }
  catch(e){ status.textContent='WebRTC could not reach WatchGrapher'; stop(); return; }
  if(!r.ok){ status.textContent='WebRTC failed: '+await r.text(); stop(); return; }
  await pc.setRemoteDescription(await r.json());
  status.textContent='streaming (WebRTC)';
  const a=ac.createAnalyser(); gainNode.connect(a);
  const d=new Uint8Array(a.fftSize);
  (function tick(){ if(!running)return; a.getByteTimeDomainData(d);
    let pk=0; for(const v of d){ const x=Math.abs(v-128)/128; if(x>pk)pk=x; }
    meter(pk); requestAnimationFrame(tick); })();
}

function stopStreams(){
  try{node&&node.disconnect();node=null;}catch(e){}
  try{gainNode&&gainNode.disconnect();gainNode=null;}catch(e){}
  try{src&&src.disconnect();src=null;}catch(e){}
  try{ws&&(ws.onclose=ws.onerror=null,ws.close());ws=null;}catch(e){}
  try{pc&&(pc.onconnectionstatechange=null,pc.close());pc=null;}catch(e){}
  try{stream&&stream.getTracks().forEach(t=>t.stop());stream=null;}catch(e){}
  try{ac&&ac.close();ac=null;}catch(e){}
}
async function stop(){
  running=false;
  goBtn.disabled=false; stopBtn.disabled=true; saveBtn.disabled=true;
  bar.style.width=0; clip.textContent=''; prog.textContent='';
  if(status.textContent.indexOf('lost')<0 && status.textContent.indexOf('failed')<0)
    status.textContent='stopped';
  releaseAwake();
  stopStreams();
  await cmd('stop');
  if(poll){ clearInterval(poll); poll=null; }
  setTimeout(refresh,300);
}
saveBtn.onclick=async()=>{ saveBtn.disabled=true; await cmd('save'); setTimeout(refresh,400); };
goBtn.onclick=start;
stopBtn.onclick=stop;
setInterval(()=>{ if(!poll) refresh(); }, 2000);
refresh();
</script></body></html>"""
