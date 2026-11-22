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
over the merge -- or let the device sync with the running app instead
(Settings -> Sync on the device; the desktop's Phone Portal tab shows the code). The desktop recomputes power-reserve figures from the samples
when it shows them, so device runs get the full analysis there.
"""
import argparse
import json
import os
import shutil
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from watchgrapher.devicesync import merge, normalise  # noqa: E402,F401  (shared with live sync)


def default_desktop_path():
    # Same rule as watchgrapher/ui.py: WATCHGRAPHER_HOME, else the checkout.
    home = os.environ.get("WATCHGRAPHER_HOME") or ROOT
    return os.path.join(home, "watches", "collection.json")


def load(src):
    if src.startswith("http://") or src.startswith("https://"):
        with urllib.request.urlopen(src.rstrip("/") + "/api/collection", timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    with open(src, encoding="utf-8") as fh:
        return json.load(fh)


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
