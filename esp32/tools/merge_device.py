"""
Merge the ESP32 device's results into the desktop app's collection.

    python esp32/tools/merge_device.py --device http://192.168.4.1
    python esp32/tools/merge_device.py --device device_collection.json [--desktop PATH]

The device stores the same collection.json schema as the desktop. This adds
what the device has and the desktop lacks -- new watches, and test /
power-reserve / service records not already present (matched by their
`when` timestamp) -- and never changes or removes anything on the desktop.
The desktop file is backed up next to itself first.

Close WatchGrapher on the desktop before running this, so it does not save
over the merge. The desktop recomputes power-reserve figures from the samples
when it shows them, so device runs get the full analysis there.
"""
import argparse
import json
import math
import os
import shutil
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

# Numbers the browser had to write as null (JSON has no NaN). The desktop's
# dataclasses expect NaN there, as its own json.dump writes them.
FLOAT_FIELDS = {
    "history": ("lift_angle", "mean_rate", "delta_rate", "max_amplitude", "min_amplitude",
                "max_beat_error"),
    "reserves": ("lift_angle", "hours", "amp_first", "amp_last", "power_reserve_h", "practical_h",
                 "hours_to_220", "hours_to_200", "iso_slope", "iso_span", "be_slope"),
}
READING_FIELDS = ("rate", "amplitude", "beat_error")
LISTS = ("history", "reserves", "services")


def default_desktop_path():
    # Same rule as watchgrapher/ui.py: WATCHGRAPHER_HOME, else the checkout.
    home = os.environ.get("WATCHGRAPHER_HOME") or ROOT
    return os.path.join(home, "watches", "collection.json")


def nan(v):
    return float("nan") if v is None else v


def normalise(watch):
    for key, fields in FLOAT_FIELDS.items():
        for rec in watch.get(key, []) or []:
            for f in fields:
                if f in rec:
                    rec[f] = nan(rec[f])
            for r in rec.get("readings", []) or []:
                for f in READING_FIELDS:
                    if f in r:
                        r[f] = nan(r[f])
            if key == "reserves":
                rec["samples"] = [[nan(v) for v in s] for s in rec.get("samples", [])]
    return watch


def load(src):
    if src.startswith("http://") or src.startswith("https://"):
        with urllib.request.urlopen(src.rstrip("/") + "/api/collection", timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    with open(src, encoding="utf-8") as fh:
        return json.load(fh)


def merge(desktop, device):
    added_w, added_r = 0, 0
    by_id = {w.get("id"): w for w in desktop.setdefault("watches", [])}
    for w in device.get("watches", []):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True, help="device URL (http://192.168.4.1) or a downloaded JSON file")
    ap.add_argument("--desktop", default="", help="desktop collection.json (default: the app's own)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    path = a.desktop or default_desktop_path()
    device = load(a.device)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            desktop = json.load(fh)
    else:
        desktop = {"version": 1, "watches": []}
    w, r = merge(desktop, device)
    print(f"{w} new watch(es), {r} new record(s) from the device")
    if a.dry_run or (w == 0 and r == 0):
        return 0
    if os.path.exists(path):
        bak = f"{path}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(path, bak)
        print(f"backup: {bak}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    desktop["saved"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(desktop, fh, indent=2)
    os.replace(tmp, path)
    print(f"merged into {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
