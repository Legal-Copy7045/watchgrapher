"""
Sync with WatchGrapher pocket devices (the ESP32-S3 version) on the LAN.

The desktop announces itself with a small UDP broadcast every few seconds; a
device on the same network hears it, lists the computer, and -- once paired
with a 6-digit code shown here -- sends its collection over HTTP. The desktop
merges it into its own (add-only: new watches, and test / service /
power-reserve records it does not have, matched by their `when`; nothing on
the desktop is changed or removed) and replies with the merged collection,
which the device keeps. Both then hold everything.

Merging happens on the app's main thread (the request is queued and the UI
timer runs it), so the app's own next save cannot overwrite a sync.

Security: the HTTP server binds to this computer's LAN address only. Pairing
needs the code on this screen (five wrong codes lock pairing for a minute);
every sync needs the token the device got when it paired, and tokens can be
revoked here. Beacons carry only this install's id, a display name and the
port.
"""

from __future__ import annotations

import hmac
import json
import math
import queue
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

SYNC_PORT = 8478          # TCP: pairing and sync
BEACON_PORT = 8479        # UDP: "a desktop is here"
BEACON_MAGIC = b"WGDESK1 "
BEACON_EVERY_S = 3.0
MAX_BODY = 16 * 1024 * 1024

# ---------------------------------------------------------------- merging

# Numbers JSON can't carry as NaN (the device writes null). The desktop's
# dataclasses expect NaN there, as its own json.dump writes them.
FLOAT_FIELDS = {
    "history": ("lift_angle", "mean_rate", "delta_rate", "max_amplitude", "min_amplitude",
                "max_beat_error"),
    "reserves": ("lift_angle", "hours", "amp_first", "amp_last", "power_reserve_h", "practical_h",
                 "hours_to_220", "hours_to_200", "iso_slope", "iso_span", "be_slope"),
}
READING_FIELDS = ("rate", "amplitude", "beat_error")
LISTS = ("history", "reserves", "services")


def _nan(v):
    return float("nan") if v is None else v


def normalise(watch: dict) -> dict:
    """null -> NaN in the numeric fields of a watch's records."""
    for key, fields in FLOAT_FIELDS.items():
        for rec in watch.get(key, []) or []:
            for f in fields:
                if f in rec:
                    rec[f] = _nan(rec[f])
            for r in rec.get("readings", []) or []:
                for f in READING_FIELDS:
                    if f in r:
                        r[f] = _nan(r[f])
            if key == "reserves":
                rec["samples"] = [[_nan(v) for v in s] for s in rec.get("samples", []) or []]
    return watch


def merge(desktop: dict, device: dict):
    """Add what `device` has and `desktop` lacks. Returns (watches, records) added."""
    added_w, added_r = 0, 0
    by_id = {w.get("id"): w for w in desktop.setdefault("watches", [])}
    for w in device.get("watches", []) or []:
        if not isinstance(w, dict) or not w.get("id"):
            continue
        w = normalise(w)
        cur = by_id.get(w.get("id"))
        if cur is None:
            desktop["watches"].append(w)
            by_id[w.get("id")] = w
            added_w += 1
            continue
        for key in LISTS:
            have = {r.get("when") for r in cur.get(key, []) or []}
            new = [r for r in w.get(key, []) or [] if r.get("when") not in have]
            if new:
                cur.setdefault(key, []).extend(new)
                cur[key].sort(key=lambda r: r.get("when") or "")
                added_r += len(new)
    return added_w, added_r


def json_safe(obj):
    """NaN / inf -> None, so the result is valid JSON for the device."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


# ---------------------------------------------------------------- server

def lan_ip() -> str:
    """This machine's address on the LAN (the route to a public address; no
    packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class SyncServer:
    """
    Beacon + HTTP endpoint. `on_sync(device_dict) -> merged_dict` runs on the
    main thread via `drain()`; `paired` is {device_id: {"token", "name",
    "paired", "last_sync"}} and `save_paired(paired)` persists it.
    """

    def __init__(self, install_id: str, name: str, paired: dict,
                 save_paired: Callable[[dict], None], port: int = SYNC_PORT):
        self.install_id = install_id
        self.name = name
        self.paired = dict(paired or {})
        self._save_paired = save_paired
        self.port = port
        self.ip = ""
        self.code = self._new_code()
        self.status = "not running"
        self._lock = threading.Lock()
        self._jobs: "queue.Queue" = queue.Queue()
        self._srv: Optional[ThreadingHTTPServer] = None
        self._stop = threading.Event()
        self._bad_codes = 0
        self._locked_until = 0.0
        self.last_event = ""

    @staticmethod
    def _new_code() -> str:
        return f"{secrets.randbelow(1000000):06d}"

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self._srv is not None:
            return
        self.ip = lan_ip()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj, headers=None):
                body = json.dumps(json_safe(obj), allow_nan=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                try:
                    n = int(self.headers.get("Content-Length", 0) or 0)
                except ValueError:
                    return None
                if n <= 0 or n > MAX_BODY:
                    return None
                data = b""
                while len(data) < n:
                    chunk = self.rfile.read(n - len(data))
                    if not chunk:
                        return None
                    data += chunk
                try:
                    return json.loads(data.decode("utf-8"))
                except ValueError:
                    return None

            def do_GET(self):
                if self.path == "/wg-sync/hello":
                    return self._json(200, {"app": "watchgrapher-desktop", "v": 1,
                                            "id": server.install_id, "name": server.name})
                self._json(404, {"ok": False})

            def do_POST(self):
                if self.path == "/wg-sync/pair":
                    return self._json(*server._pair(self._body()))
                if self.path == "/wg-sync/sync":
                    dev = self.headers.get("X-WG-Device", "")
                    tok = self.headers.get("X-WG-Token", "")
                    if not server._authorised(dev, tok):
                        return self._json(403, {"ok": False, "msg": "Not paired with this computer."})
                    body = self._body()
                    if not isinstance(body, dict) or not isinstance(body.get("watches", []), list):
                        return self._json(400, {"ok": False, "msg": "Expected a collection."})
                    return self._json(*server._run_on_main(dev, body))
                self._json(404, {"ok": False})

        class _Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._srv = _Server((self.ip, self.port), Handler)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self._stop.clear()
        threading.Thread(target=self._beacon, daemon=True).start()
        self.status = f"listening on {self.ip}:{self.port}"

    def stop(self):
        self._stop.set()
        if self._srv is not None:
            try:
                self._srv.shutdown()
                self._srv.server_close()
            except Exception:
                pass
            self._srv = None
        self.status = "not running"
        # Fail anything still waiting.
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            job["result"] = (503, {"ok": False, "msg": "WatchGrapher is closing."})
            job["done"].set()

    @property
    def running(self) -> bool:
        return self._srv is not None

    def _beacon(self):
        msg_base = {"id": self.install_id, "name": self.name, "port": self.port, "v": 1}
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            # Send from the LAN interface, so the broadcast leaves on the network
            # the devices are on.
            try:
                s.bind((self.ip, 0))
            except OSError:
                pass
            while not self._stop.wait(BEACON_EVERY_S):
                pkt = BEACON_MAGIC + json.dumps(msg_base).encode()
                for dst in ("255.255.255.255", self._subnet_broadcast()):
                    try:
                        s.sendto(pkt, (dst, BEACON_PORT))
                    except OSError:
                        pass
        finally:
            s.close()

    def _subnet_broadcast(self) -> str:
        # Assumes the common /24 home network; 255.255.255.255 covers the rest.
        parts = self.ip.split(".")
        return ".".join(parts[:3] + ["255"]) if len(parts) == 4 else "255.255.255.255"

    # -- pairing -----------------------------------------------------------
    def _pair(self, body):
        if not isinstance(body, dict):
            return 400, {"ok": False, "msg": "Bad request."}
        now = time.time()
        with self._lock:
            if now < self._locked_until:
                return 429, {"ok": False, "msg": "Too many wrong codes -- wait a minute."}
            code = str(body.get("code", "")).strip()
            dev = str(body.get("device_id", "")).strip()[:64]
            if not dev or not hmac.compare_digest(code, self.code):
                self._bad_codes += 1
                if self._bad_codes >= 5:
                    self._locked_until = now + 60
                    self._bad_codes = 0
                return 403, {"ok": False, "msg": "That code doesn't match the one on the computer."}
            self._bad_codes = 0
            token = secrets.token_hex(16)
            self.paired[dev] = {"token": token, "name": str(body.get("device_name", ""))[:64] or dev,
                                "paired": time.strftime("%Y-%m-%dT%H:%M:%S"), "last_sync": ""}
            self.code = self._new_code()   # one use
            paired = dict(self.paired)
        self._save_paired(paired)
        self.last_event = f"paired with {self.paired[dev]['name']}"
        return 200, {"ok": True, "token": token, "id": self.install_id, "name": self.name}

    def _authorised(self, dev: str, tok: str) -> bool:
        with self._lock:
            p = self.paired.get(dev)
            return bool(p and tok and hmac.compare_digest(str(p.get("token", "")), tok))

    def forget(self, dev: Optional[str] = None):
        with self._lock:
            if dev is None:
                self.paired.clear()
            else:
                self.paired.pop(dev, None)
            paired = dict(self.paired)
        self._save_paired(paired)

    # -- sync on the main thread --------------------------------------------
    def _run_on_main(self, dev: str, body: dict):
        job = {"dev": dev, "body": body, "done": threading.Event(), "result": None}
        self._jobs.put(job)
        if not job["done"].wait(30):
            return 504, {"ok": False, "msg": "WatchGrapher on the computer didn't answer in time."}
        return job["result"]

    def drain(self, on_sync: Callable[[dict], dict]):
        """Run queued syncs. Call from the main (UI) thread."""
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            try:
                merged, added = on_sync(job["body"])
                with self._lock:
                    p = self.paired.get(job["dev"])
                    if p is not None:
                        p["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    paired = dict(self.paired)
                    name = (p or {}).get("name", job["dev"])
                self._save_paired(paired)
                self.last_event = f"synced with {name}: {added[0]} new watch(es), {added[1]} new record(s)"
                # The body is the merged collection itself, so the device (which
                # has no JSON parser) can store it as it arrives; the counts go
                # in headers.
                job["result"] = (200, merged, {"X-WG-Added-Watches": str(added[0]),
                                               "X-WG-Added-Records": str(added[1])})
            except Exception as e:  # pragma: no cover - reported to the device
                job["result"] = (500, {"ok": False, "msg": f"Sync failed on the computer: {e}"})
            job["done"].set()
