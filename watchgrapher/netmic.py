"""
Phone (or any browser) as a remote pickup.

Runs a small HTTP server on the LAN. Open the URL it prints on a phone, tap
Start, and the browser streams microphone audio back -- either as raw Int16
PCM over a hand-rolled WebSocket (no dependencies, the default) or over
WebRTC (needs `aiortc`, better on a marginal Wi-Fi link). Either way the
audio lands in a ring buffer that presents the same surface as
audio.Recorder, so the rest of the app treats it as just another input.

Nothing here is exposed beyond the local network, and the server only ever
receives audio -- it never plays anything back or runs code from the page.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import numpy as np

try:
    import aiortc  # noqa: F401
    HAVE_AIORTC = True
except Exception:
    HAVE_AIORTC = False

_WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


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
        self.gain = 1.0
        self.opened_note = ""
        self._want_port = port
        self._srv: Optional[ThreadingHTTPServer] = None
        self._srv_thread: Optional[threading.Thread] = None
        self.port = 0
        self.url = ""
        self.connected = False
        self._last_rx = 0.0
        self._rtc_pcs = set()
        self._rtc_loop = None

    # -- lifecycle --------------------------------------------------------------
    def start(self):
        rec = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):        # keep the console quiet
                pass

            def _send(self, code, body=b"", ctype="text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif self.path == "/ws":
                    rec._serve_ws(self)
                elif self.path == "/rtc-available":
                    self._send(200, json.dumps({"aiortc": HAVE_AIORTC}).encode(),
                               "application/json")
                else:
                    self._send(404)

            def do_POST(self):
                if self.path == "/rtc-offer":
                    n = int(self.headers.get("Content-Length", 0))
                    offer = json.loads(self.rfile.read(n) or b"{}")
                    try:
                        answer = rec._rtc_answer(offer)
                        self._send(200, json.dumps(answer).encode(), "application/json")
                    except Exception as e:
                        self._send(500, str(e).encode())
                else:
                    self._send(404)

        self._srv = ThreadingHTTPServer(("0.0.0.0", self._want_port), Handler)
        self.port = self._srv.server_address[1]
        self.url = f"http://{lan_ip()}:{self.port}"
        self.opened_note = (f"Phone pickup ready at {self.url} -- open it on a device on "
                            f"the same network and tap Start.")
        self._srv_thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._srv_thread.start()

    def stop(self):
        if self._srv is not None:
            try:
                self._srv.shutdown()
                self._srv.server_close()
            except Exception:
                pass
            self._srv = None
        loop = self._rtc_loop
        if loop is not None:
            for pc in list(self._rtc_pcs):
                try:
                    loop.call_soon_threadsafe(lambda p=pc: loop.create_task(p.close()))
                except Exception:
                    pass

    # -- Recorder surface -----------------------------------------------------
    @property
    def running(self):
        return self._srv is not None

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
        self.connected = True
        self._last_rx = time.time()
        p = float(np.max(np.abs(data)))
        self.peak = max(self.peak * 0.92, p)
        if p >= 0.999:
            self.clips += 1
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
        key = handler.headers.get("Sec-WebSocket-Key", "")
        if not key:
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
        src_sr = float(self.samplerate)
        try:
            while self._srv is not None:
                frame = _ws_read_frame(conn)
                if frame is None:
                    break
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
        finally:
            self.connected = False

    # -- WebRTC (optional) ----------------------------------------------------
    def _rtc_answer(self, offer: dict) -> dict:
        if not HAVE_AIORTC:
            raise RuntimeError("aiortc not installed")
        import asyncio
        from aiortc import RTCPeerConnection, RTCSessionDescription
        from aiortc.mediastreams import MediaStreamError

        if self._rtc_loop is None:
            self._rtc_loop = asyncio.new_event_loop()
            threading.Thread(target=self._rtc_loop.run_forever, daemon=True).start()
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

def _recv_exact(conn, n):
    out = b""
    while len(out) < n:
        chunk = conn.recv(n - len(out))
        if not chunk:
            return None
        out += chunk
    return out


def _ws_read_frame(conn):
    hdr = _recv_exact(conn, 2)
    if hdr is None:
        return None
    b0, b1 = hdr[0], hdr[1]
    opcode = b0 & 0x0F
    masked = b1 & 0x80
    length = b1 & 0x7F
    if length == 126:
        ext = _recv_exact(conn, 2)
        if ext is None:
            return None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = _recv_exact(conn, 8)
        if ext is None:
            return None
        length = struct.unpack(">Q", ext)[0]
    mask = _recv_exact(conn, 4) if masked else b"\x00\x00\x00\x00"
    if mask is None:
        return None
    payload = _recv_exact(conn, length) if length else b""
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
<title>WatchGrapher pickup</title>
<style>
  body{font-family:-apple-system,Segoe UI,sans-serif;background:#12161c;color:#e8eef7;
       margin:0;padding:24px;text-align:center}
  h1{font-size:18px;font-weight:600}
  button{font-size:18px;padding:16px 28px;border-radius:10px;border:0;margin:8px;
         background:#4da3ff;color:#08101c;font-weight:700}
  button.stop{background:#ff5d5d}
  #meter{height:16px;background:#1a1f27;border-radius:8px;margin:18px auto;max-width:320px}
  #bar{height:100%;width:0;background:#57d38c;border-radius:8px}
  .row{margin:14px 0}
  label{font-size:15px}
  #status{color:#8a94a4;font-size:14px;min-height:20px}
  select{font-size:15px;padding:6px}
</style></head><body>
<h1>WatchGrapher pickup</h1>
<p id="status">idle</p>
<div class="row">
  <label><input type="radio" name="mode" value="pcm" checked> PCM (WebSocket)</label>
  &nbsp;&nbsp;
  <label><input type="radio" name="mode" value="rtc" id="rtcopt"> WebRTC</label>
</div>
<div id="meter"><div id="bar"></div></div>
<button id="go">Start</button>
<button id="stop" class="stop" disabled>Stop</button>
<p id="hint" style="color:#5a6472;font-size:13px">
Keep this screen on. Put the phone mic close to the movement.</p>
<script>
let ac, node, stream, ws, pc, running=false;
const status=document.getElementById('status'), bar=document.getElementById('bar');
fetch('/rtc-available').then(r=>r.json()).then(j=>{
  if(!j.aiortc){document.getElementById('rtcopt').disabled=true;
    document.getElementById('rtcopt').parentNode.style.opacity=.4;
    document.getElementById('rtcopt').parentNode.title='Server was started without aiortc';}
});
function mode(){return document.querySelector('input[name=mode]:checked').value;}
async function start(){
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,
      noiseSuppression:false,autoGainControl:false}});
  }catch(e){status.textContent='mic denied: '+e; return;}
  running=true;
  document.getElementById('go').disabled=true;
  document.getElementById('stop').disabled=false;
  if(mode()==='rtc'){ await startRtc(); } else { await startPcm(); }
}
async function startPcm(){
  ac=new (window.AudioContext||window.webkitAudioContext)();
  ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');
  ws.binaryType='arraybuffer';
  ws.onopen=()=>{ ws.send(JSON.stringify({sr:ac.sampleRate})); status.textContent='streaming (PCM) @ '+Math.round(ac.sampleRate)+' Hz'; };
  ws.onclose=()=>{ if(running) stop(); };
  const src=ac.createMediaStreamSource(stream);
  node=ac.createScriptProcessor(2048,1,1);
  node.onaudioprocess=e=>{
    const f=e.inputBuffer.getChannelData(0);
    let pk=0; const buf=new Int16Array(f.length);
    for(let i=0;i<f.length;i++){ const s=Math.max(-1,Math.min(1,f[i]));
      buf[i]=s<0?s*32768:s*32767; if(Math.abs(s)>pk)pk=Math.abs(s); }
    bar.style.width=Math.min(100,pk*140)+'%';
    if(ws.readyState===1) ws.send(buf.buffer);
  };
  src.connect(node); node.connect(ac.destination);
}
async function startRtc(){
  pc=new RTCPeerConnection();
  stream.getTracks().forEach(t=>pc.addTrack(t,stream));
  const offer=await pc.createOffer({offerToReceiveAudio:false});
  await pc.setLocalDescription(offer);
  await new Promise(res=>{ if(pc.iceGatheringState==='complete')res();
    else pc.onicegatheringstatechange=()=>{ if(pc.iceGatheringState==='complete')res(); }; });
  const r=await fetch('/rtc-offer',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(pc.localDescription)});
  if(!r.ok){ status.textContent='WebRTC failed: '+await r.text(); stop(); return; }
  await pc.setRemoteDescription(await r.json());
  status.textContent='streaming (WebRTC)';
  const mon=ac=new (window.AudioContext||window.webkitAudioContext)();
  const s=mon.createMediaStreamSource(stream), a=mon.createAnalyser();
  s.connect(a); const d=new Uint8Array(a.fftSize);
  (function tick(){ if(!running)return; a.getByteTimeDomainData(d);
    let pk=0; for(const v of d){ const x=Math.abs(v-128)/128; if(x>pk)pk=x; }
    bar.style.width=Math.min(100,pk*160)+'%'; requestAnimationFrame(tick); })();
}
function stop(){
  running=false;
  document.getElementById('go').disabled=false;
  document.getElementById('stop').disabled=true;
  status.textContent='stopped'; bar.style.width=0;
  try{node&&node.disconnect();}catch(e){}
  try{ws&&ws.close();}catch(e){}
  try{pc&&pc.close();}catch(e){}
  try{stream&&stream.getTracks().forEach(t=>t.stop());}catch(e){}
  try{ac&&ac.close();}catch(e){}
}
document.getElementById('go').onclick=start;
document.getElementById('stop').onclick=stop;
</script></body></html>"""
