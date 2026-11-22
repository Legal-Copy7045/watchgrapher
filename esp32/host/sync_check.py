"""
Check the desktop side of device sync (watchgrapher/devicesync.py) the way the
device uses it: pairing with the code, syncing with the token, the add-only
merge, and revocation -- with the merge run on a stand-in "main thread", as
the app's UI timer does.

    python esp32/host/sync_check.py
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from watchgrapher import devicesync  # noqa: E402

fails = 0


def check(cond, what):
    global fails
    print(("ok   " if cond else "FAIL ") + what)
    fails += not cond


def post(url, body, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def main():
    desktop = {"version": 1, "watches": [
        {"id": "d1", "brand": "Hillcrest", "model": "Diver", "nickname": "desk name",
         "history": [{"when": "2026-01-01T10:00:00", "mean_rate": float("nan")}]},
    ]}
    saved = {}
    srv = devicesync.SyncServer("desk-1", "Test desktop", {}, lambda p: saved.update(p), port=18478)
    srv.start()
    stop = threading.Event()

    def main_thread():
        def on_sync(dev):
            added = devicesync.merge(desktop, dev)
            return desktop, added
        while not stop.is_set():
            srv.drain(on_sync)
            time.sleep(0.05)

    threading.Thread(target=main_thread, daemon=True).start()
    base = f"http://{srv.ip}:{srv.port}"
    try:
        st, j, _ = post(base + "/wg-sync/pair", {"device_id": "dev-1", "code": "000000" if srv.code != "000000" else "111111"})
        check(st == 403 and not j["ok"], "a wrong code is refused")
        code = srv.code
        st, j, _ = post(base + "/wg-sync/pair", {"device_id": "dev-1", "device_name": "Pocket", "code": code})
        check(st == 200 and j.get("token"), "the right code pairs and returns a token")
        tok = j.get("token", "")
        check(srv.code != code, "the code changes after use")
        check("dev-1" in saved, "the pairing is saved")

        st, _, _ = post(base + "/wg-sync/sync", {"watches": []}, {"X-WG-Device": "dev-1", "X-WG-Token": "nope"})
        check(st == 403, "a sync with a bad token is refused")

        device = {"version": 1, "watches": [
            {"id": "d1", "brand": "Hillcrest", "model": "Diver", "nickname": "device name",
             "history": [{"when": "2026-01-01T10:00:00", "mean_rate": 9.0},     # already on desktop
                         {"when": "2026-02-01T10:00:00", "mean_rate": None}]},  # new
            {"id": "v1", "brand": "Lakeview", "model": "Field", "history": []},
        ]}
        st, merged, hdr = post(base + "/wg-sync/sync", device, {"X-WG-Device": "dev-1", "X-WG-Token": tok})
        check(st == 200, "a paired device can sync")
        check(hdr.get("X-WG-Added-Watches") == "1" and hdr.get("X-WG-Added-Records") == "1",
              "the counts come back in headers")
        ids = [w["id"] for w in merged.get("watches", [])]
        check(ids == ["d1", "v1"], "the reply is the merged collection itself")
        d1 = next(w for w in desktop["watches"] if w["id"] == "d1")
        check(d1["nickname"] == "desk name", "the desktop's watch details are not overwritten")
        check(len(d1["history"]) == 2, "only the new record is added")
        check(d1["history"][0]["mean_rate"] != d1["history"][0]["mean_rate"],
              "an existing record is left as it was (NaN stays NaN)")
        check(d1["history"][1]["mean_rate"] != d1["history"][1]["mean_rate"], "null arrives as NaN")
        check(json.dumps(merged, allow_nan=False) is not None, "the reply is valid JSON (no NaN)")

        st, _, hdr = post(base + "/wg-sync/sync", device, {"X-WG-Device": "dev-1", "X-WG-Token": tok})
        check(st == 200 and hdr.get("X-WG-Added-Records") == "0", "syncing again adds nothing")

        srv.forget("dev-1")
        st, _, _ = post(base + "/wg-sync/sync", device, {"X-WG-Device": "dev-1", "X-WG-Token": tok})
        check(st == 403, "a forgotten device can no longer sync")

        for _ in range(5):
            post(base + "/wg-sync/pair", {"device_id": "dev-2", "code": "x"})
        st, j, _ = post(base + "/wg-sync/pair", {"device_id": "dev-2", "code": srv.code})
        check(st == 429, "five wrong codes lock pairing")
    finally:
        stop.set()
        srv.stop()
    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
