"""
Development server for the device's web app -- no ESP32 needed.

Serves esp32/web/ and imitates the firmware's HTTP API (src/web.cpp,
src/engine.cpp) with the desktop's simulated watch and Python analyzer behind
it, so the page can be worked on and tested in a desktop browser:

    python esp32/host/devserver.py [--port 8080] [--speed 60]

--speed makes power-reserve logs run that many times faster than real time
(and the simulated watch's amplitude decay with it), so a reserve run can be
watched to the end in minutes. State lives in a temporary folder.
"""
import argparse
import json
import math
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
WEB = os.path.join(ROOT, "esp32", "web")
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
from watchgrapher import analysis  # noqa: E402
from watchgrapher.audio import SimulatedRecorder  # noqa: E402

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript",
        ".css": "text/css", ".json": "application/json"}


def nn(v, d=2):
    """Number for JSON: NaN/inf -> None, like the firmware's json::num."""
    if v is None:
        return None
    v = float(v)
    return round(v, d) if math.isfinite(v) else None


class Device:
    def __init__(self, data_dir, speed):
        self.dir = data_dir
        os.makedirs(os.path.join(data_dir, "pr"), exist_ok=True)
        self.speed = speed
        self.lock = threading.RLock()
        self.settings = dict(
            source="sim", sample_rate=48000, pin_bclk=5, pin_ws=6, pin_din=4, pin_mclk=-1,
            i2s_right=0, gain_db=18.0, agc=True, window_s=12.0, window_cap=13.5,
            band_lo=1500.0, band_hi=12000.0, env_win_ms=0.35, sub_threshold=0.16,
            lift_angle=52.0, forced_bph=0, exact_amplitude=True, no_parity_fix=False,
            sim_bph=28800, sim_amp=285.0, sim_lift=52.0, sim_rate=4.0, sim_be=0.3, sim_snr=18.0,
            ap_ssid="", ap_ssid_live="WatchGrapher-DEV", sta_ssid="", sta_ip="", ap_fallback=True,
            net_status="No home network set \u00b7 hotspot WatchGrapher-DEV on",
            hostname="watchgrapher", pin_led=48, fw="0.2.0-dev", variant="n16r8",
            psram_free=1_600_000, heap_free=210_000, fs_total=10_354_688, fs_used=40_960)
        self.rec = SimulatedRecorder(samplerate=48000, buffer_seconds=20.0)
        self._apply_sim()
        self.listening = False
        self.seq = 0
        self.m = None
        self.an_ms = 0.0
        self.hist = []
        self.run = {"state": "idle"}
        self.pr = {"active": False}
        self.t0 = time.monotonic()
        threading.Thread(target=self._loop, daemon=True).start()

    # -- helpers -----------------------------------------------------------
    def now(self):
        return time.monotonic() - self.t0

    def _apply_sim(self):
        s = self.settings
        self.rec.set_params(bph=s["sim_bph"], amplitude=s["sim_amp"], lift_angle=s["sim_lift"],
                            rate_spd=s["sim_rate"], beat_error_ms=s["sim_be"], snr_db=s["sim_snr"])

    def capturing(self):
        return (self.listening or self.pr["active"]
                or self.run["state"] in ("settle", "measure"))

    # -- analysis loop -----------------------------------------------------
    def _loop(self):
        while True:
            want = self.capturing()
            if want and not self.rec.running:
                self.rec.start()
            if not want:
                if self.rec.running:
                    self.rec.stop()
                    self.rec.clear()
                time.sleep(0.2)
                continue
            # Power reserve: let the simulated watch run down, sped up.
            if self.pr["active"]:
                hrs = (self.now() - self.pr["t0"]) * self.speed / 3600.0
                amp = max(90.0, 300.0 - 3.6 * hrs - 0.02 * hrs * hrs)
                # Change the impulse interval directly: set_params would
                # re-anchor the beat phase and wreck the rate of every window.
                if abs(amp - self.rec.amplitude) >= 0.5:
                    self.rec.amplitude = amp
                    t_osc = 7200.0 / self.rec.bph
                    self.rec.dt = (t_osc / math.pi) * math.asin(
                        min(1.0, (self.rec.lift_angle / 2.0) / amp))
            t = self.now()
            with self.lock:
                win = self.settings["window_s"]
                if self.run["state"] == "settle" and t - self.run["t0"] >= self.run["settle"]:
                    self.run.update(state="measure", ms=t)
                final = False
                if self.run["state"] == "measure":
                    meas = t - self.run["ms"]
                    if meas >= self.run["duration"] or self.run.get("stop"):
                        final = True
                    win = min(win, meas, self.run["duration"])
            if win < 3:
                if final:
                    with self.lock:
                        self._finish_run()
                time.sleep(0.25)
                continue
            x = self.rec.read(win)
            s = self.settings
            cfg = analysis.AnalyzerConfig(band_lo=s["band_lo"], band_hi=s["band_hi"],
                                          env_win_ms=s["env_win_ms"], sub_threshold=s["sub_threshold"],
                                          lift_angle=s["lift_angle"],
                                          forced_bph=s["forced_bph"] or None,
                                          exact_amplitude=s["exact_amplitude"],
                                          no_parity_fix=s["no_parity_fix"])
            t1 = time.perf_counter()
            m = analysis.analyze(x, 48000, cfg)
            ms = (time.perf_counter() - t1) * 1000
            js = self._mjson(m, ms)
            with self.lock:
                self.m, self.an_ms = js, ms
                self.seq += 1
                self.hist.append([round(t, 1), nn(m.rate), nn(m.amplitude, 1), nn(m.beat_error)]
                                 if m.ok else [round(t, 1), None, None, None])
                self.hist = self.hist[-900:]
                if self.run["state"] == "measure":
                    if m.ok:
                        for k, v in (("rate", m.rate), ("ci", m.rate_ci), ("amp", m.amplitude),
                                     ("be", m.beat_error), ("len", len(x) / 48000.0)):
                            self.run[k].append(v)
                        self.run["bph"] = m.nominal_bph
                    if final:
                        self._finish_run()
                if self.pr["active"]:
                    p = self.pr
                    if m.ok:
                        p["buf"].append((m.rate, m.amplitude, m.beat_error))
                    while self.pr["active"] and self.now() >= self.pr["next"]:
                        self._pr_sample()
            time.sleep(0.5)

    def _mjson(self, m, ms):
        o = dict(ok=m.ok, msg=m.message, bph=m.detected_bph or 0, nominal=m.nominal_bph or 0,
                 raw_bph=nn(m.raw_bph), rate=nn(m.rate), ci=nn(m.rate_ci), be=nn(m.beat_error),
                 amp=nn(m.amplitude, 1), amp_iqr=nn(m.amplitude_spread, 1), beats=m.beats,
                 dur=nn(m.duration), snr=nn(m.snr_db, 1), q=nn(m.quality, 3), lift=nn(m.lift_angle, 1),
                 dt_ms=nn(m.dt_mean * 1000, 3), par_ms=nn(m.parity_correction), par_seen_ms=nn(m.parity_offset_seen),
                 extra=nn(m.extra_peaks), valid=nn(m.valid_frac, 3), an_ms=round(ms))
        if m.ok and m.times.size >= 4:
            npd = 3600.0 / m.nominal_bph
            dev = ((m.times - m.times[0]) - m.index * npd) * 1000.0
            c = float(np.median(dev[:max(4, len(dev) // 20)]))
            o["trace"] = [[round(d - c, 3), round(float(t - m.times[0]), 3), int(i) % 2]
                          for d, t, i in zip(dev, m.times, m.index)]
        if m.mean_shape is not None:
            L = len(m.mean_shape)
            step = max(1, (L + 319) // 320)
            o["shape"] = [round(float(m.mean_shape[i:i + step].max()), 3) for i in range(0, L, step)]
            k = 1000.0 / m.shape_fs
            o.update(shape_ms=L * k, shape_pre_ms=m.shape_pre * k, p1_ms=m.p1_idx * k, p3_ms=m.p3_idx * k)
        if m.inst_rate.size:
            step = max(1, (m.inst_rate.size + 159) // 160)
            o["inst"] = [[round(float(a), 2), round(float(b), 1)]
                         for a, b in zip(m.inst_rate_t[::step], m.inst_rate[::step])]
        return o

    def _finish_run(self):
        r = self.run
        # As the firmware: only full-length windows count (engine.cpp finish_run).
        if r["rate"]:
            want = min(max(r["len"]), self.settings["window_s"], r["duration"]) * 0.95
            keep = [i for i, n in enumerate(r["len"]) if n >= want]
            for k in ("rate", "ci", "amp", "be", "len"):
                r[k] = [r[k][i] for i in keep]
        res = dict(position=r["position"], wind=r["wind"], watch_id=r["watch_id"],
                   caliber_key=r["caliber_key"], when=time.strftime("%Y-%m-%dT%H:%M:%S"),
                   lift_angle=r["lift"], bph=r.get("bph", 0), windows=len(r["rate"]),
                   seconds=round(min(self.now() - r.get("ms", self.now()), r["duration"]), 1))
        if not r["rate"]:
            res.update(ok=False, msg="No usable reading during the run. Check the pickup and try again.")
        else:
            rate = float(np.mean(r["rate"]))
            win = min(self.settings["window_s"], r["duration"])
            n_ind = max(1.0, res["seconds"] / max(1.0, win))
            sd = float(np.std(r["rate"], ddof=1)) if len(r["rate"]) > 1 else 0.0
            ci = max(math.sqrt(np.nanmean(np.square(r["ci"]))), 1.96 * sd) / math.sqrt(n_ind)
            amps = [a for a in r["amp"] if math.isfinite(a)]
            res.update(ok=True, rate=nn(rate), rate_ci=nn(ci),
                       amplitude=nn(np.median(amps), 1) if amps else None,
                       beat_error=nn(np.nanmedian(r["be"])), msg="OK")
        r["result"] = res
        r["state"] = "done"

    def _pr_sample(self):
        p = self.pr
        p["next"] += p["interval"] / self.speed
        if not p["buf"]:
            p["misses"] += 1
            if p["misses"] >= 3 and p["samples"]:
                self._pr_end("stopped")
            return
        p["misses"] = 0
        el = (self.now() - p["t0"]) * self.speed
        a = np.array(p["buf"], dtype=float)
        p["buf"] = []
        v = [el, *np.nanmedian(a, axis=0)]
        # The real watch stops near 135 deg; the simulator would keep going.
        if v[2] < 135:
            p["misses"] = 3
            self._pr_end("stopped")
            return
        with open(os.path.join(self.dir, "pr", p["id"] + ".csv"), "a") as fh:
            fh.write(",".join("" if not math.isfinite(x) else f"{x:.0f}" if i == 0 else f"{x:.3f}"
                              for i, x in enumerate(v)) + "\n")
        p["last"] = [nn(x) for x in v]
        p["samples"] += 1
        p["spark"] = (p["spark"] + [nn(v[2], 1)])[-240:]

    def _pr_end(self, reason):
        p = self.pr
        if not p["active"]:
            return
        with open(os.path.join(self.dir, "pr", p["id"] + ".end"), "w") as fh:
            json.dump(dict(reason=reason, ended=time.strftime("%Y-%m-%dT%H:%M:%S"),
                           hours=round((self.now() - p["t0"]) * self.speed / 3600, 3),
                           samples=p["samples"]), fh)
        self.pr = {"active": False}
        self._apply_sim()

    # -- API ---------------------------------------------------------------
    def state(self, since):
        with self.lock:
            t = self.now()
            r = self.run
            run = {"state": r["state"]}
            if r["state"] != "idle":
                run.update(elapsed=round(t - r["t0"], 1), settle=r["settle"], duration=r["duration"],
                           position=r["position"], wind=r["wind"], watch_id=r["watch_id"],
                           windows=len(r["rate"]))
                if r["state"] == "measure":
                    run["measured"] = round(t - r["ms"], 1)
                if r["state"] == "done":
                    run["result"] = r["result"]
            p = self.pr
            pr = {"active": p["active"]}
            if p["active"]:
                pr.update(id=p["id"], elapsed=round((t - p["t0"]) * self.speed), interval=p["interval"],
                          samples=p["samples"], next=round((p["next"] - t) * self.speed),
                          last=p["last"], spark=p["spark"],
                          spark0=p["samples"] - len(p["spark"]))
            pk = getattr(self.rec, "peak", 0.0)
            o = dict(seq=self.seq, up=round(t), listening=self.listening, capturing=self.capturing(),
                     src=self.settings["source"], fs=48000,
                     level=round(20 * math.log10(pk), 1) if pk > 1e-6 else -120,
                     gain=self.settings["gain_db"], agc=self.settings["agc"], clips=0,
                     window=self.settings["window_s"], window_cap=self.settings["window_cap"],
                     an_ms=round(self.an_ms), clock=True, run=run, pr=pr,
                     input="No USB microphone connected." if self.settings["source"] == "usb" else "",
                     input_ready=self.settings["source"] != "usb")
            if since != self.seq:
                o["m"] = self.m
            return o

    def command(self, a, q):
        g = lambda k, d="": q.get(k, [d])[0]  # noqa: E731
        with self.lock:
            if a == "listen":
                self.listening = g("on") != "0"
            elif a == "run_start":
                if self.run["state"] in ("settle", "measure"):
                    return dict(ok=False, msg="A run is already in progress.")
                self.run = dict(state="settle", t0=self.now(), settle=min(120.0, max(0.0, float(g("settle", "0") or 0))),
                                duration=min(3600.0, max(6.0, float(g("duration", "30") or 30))),
                                position=g("position"), wind=g("wind"), watch_id=g("watch_id"),
                                caliber_key=g("caliber_key"), lift=self.settings["lift_angle"],
                                rate=[], ci=[], amp=[], be=[], len=[])
            elif a == "run_stop":
                if self.run["state"] == "settle":
                    self.run = {"state": "idle"}
                elif self.run["state"] == "measure":
                    self.run["stop"] = True
            elif a == "run_clear":
                if self.run["state"] == "done":
                    self.run = {"state": "idle"}
            elif a == "pr_start":
                if self.pr["active"]:
                    return dict(ok=False, msg="A power-reserve log is already running.")
                pid = time.strftime("%Y%m%d-%H%M%S")
                interval = max(30, min(3600, int(g("interval", "300"))))
                self.pr = dict(active=True, id=pid, t0=self.now(), interval=interval,
                               next=self.now() + interval / self.speed, samples=0, misses=0,
                               buf=[], last=[None] * 4, spark=[])
                with open(os.path.join(self.dir, "pr", pid + ".json"), "w") as fh:
                    json.dump(dict(id=pid, when=time.strftime("%Y-%m-%dT%H:%M:%S"), watch_id=g("watch_id"),
                                   caliber_key=g("caliber_key"), lift_angle=self.settings["lift_angle"],
                                   interval_s=interval, target_h=float(g("target_h", "0") or 0),
                                   notes=g("notes")), fh)
            elif a == "pr_stop":
                self._pr_end("stopped by user")
            elif a == "pr_delete":
                pid = g("id")
                if not pid or "/" in pid or ".." in pid:
                    return dict(ok=False, msg="Bad id.")
                for ext in (".json", ".csv", ".end"):
                    try:
                        os.remove(os.path.join(self.dir, "pr", pid + ext))
                    except OSError:
                        pass
            elif a == "history_clear":
                self.hist = []
            else:
                return dict(ok=False, msg="Unknown action.")
        return dict(ok=True, msg="ok")

    def pr_list(self):
        out = []
        d = os.path.join(self.dir, "pr")
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            pid = fn[:-5]
            meta = json.load(open(os.path.join(d, fn)))
            end = None
            if os.path.exists(os.path.join(d, pid + ".end")):
                end = json.load(open(os.path.join(d, pid + ".end")))
            csv = os.path.join(d, pid + ".csv")
            out.append(dict(meta=meta, end=end, active=bool(self.pr["active"] and self.pr["id"] == pid),
                            csv_bytes=os.path.getsize(csv) if os.path.exists(csv) else 0))
        return out

    def set_settings(self, q):
        for k, v in q.items():
            v = v[0]
            if k not in self.settings or k == "now":
                continue
            cur = self.settings[k]
            if isinstance(cur, bool):
                self.settings[k] = v in ("1", "true")
            elif isinstance(cur, int):
                self.settings[k] = int(float(v))
            elif isinstance(cur, float):
                self.settings[k] = float(v)
            else:
                self.settings[k] = v
        self.settings["window_s"] = min(self.settings["window_s"], self.settings["window_cap"])
        self._apply_sim()
        return dict(ok=True, reboot=False, msg="Saved.")


def make_handler(dev):
    coll_path = os.path.join(dev.dir, "collection.json")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body, allow_nan=False).encode() if ctype == "application/json" else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/api/state":
                return self._send(200, dev.state(int(q.get("since", ["-1"])[0])))
            if u.path == "/api/history":
                return self._send(200, dev.hist)
            if u.path == "/api/settings":
                return self._send(200, dev.settings)
            if u.path == "/api/collection":
                if os.path.exists(coll_path):
                    return self._send(200, open(coll_path, "rb").read())
                return self._send(200, {"version": 1, "watches": []})
            if u.path == "/api/pr":
                return self._send(200, dev.pr_list())
            if u.path == "/api/pr/csv":
                p = os.path.join(dev.dir, "pr", q.get("id", [""])[0] + ".csv")
                return self._send(200, open(p).read() if os.path.exists(p) else "", "text/csv")
            name = "index.html" if u.path == "/" else u.path.lstrip("/")
            p = os.path.join(WEB, name)
            if "/" not in name and os.path.isfile(p):
                return self._send(200, open(p, "rb").read(), MIME.get(os.path.splitext(p)[1], "application/octet-stream"))
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if u.path == "/api/collection":
                try:
                    json.loads(raw)
                except ValueError:
                    return self._send(400, {"ok": False, "msg": "Expected a JSON object."})
                with open(coll_path, "wb") as fh:
                    fh.write(raw)
                return self._send(200, {"ok": True})
            q = parse_qs(raw.decode())
            if u.path == "/api/cmd":
                return self._send(200, dev.command(q.get("action", [""])[0], q))
            if u.path == "/api/settings":
                return self._send(200, dev.set_settings(q))
            if u.path in ("/api/reboot", "/api/factory_reset"):
                return self._send(200, {"ok": True})
            self._send(404, {"ok": False, "msg": "not found"})

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--speed", type=float, default=60.0,
                    help="power-reserve time acceleration (keep interval/speed above ~1 s)")
    ap.add_argument("--data", default="")
    a = ap.parse_args()
    data = a.data or tempfile.mkdtemp(prefix="wg-dev-")
    dev = Device(data, a.speed)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(dev))
    print(f"WatchGrapher dev server on http://127.0.0.1:{a.port}  (data in {data})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
