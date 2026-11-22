"""
Export the desktop caliber database to esp32/web/calibers.json for the
device's caliber picker.

    python esp32/tools/export_calibers.py

Rows are [key, brand, name, bph (0 = unpublished), lift_angle, amp_lo, amp_hi,
regulator, rated power reserve hours (0 = unknown)], sorted by brand then name. Re-run after calibers.py changes.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from watchgrapher import calibers  # noqa: E402


def main():
    rows = []
    for key, c in calibers.CALIBERS.items():
        lo, hi = c.amp_full_wind or (0, 0)
        rows.append([key, c.brand, c.name, int(c.bph or 0), round(float(c.lift_angle), 1),
                     int(lo), int(hi), c.regulator or "",
                     round(float(c.power_reserve_h or 0), 1)])
    rows.sort(key=lambda r: (r[1].lower(), r[2].lower()))
    out = os.path.join(ROOT, "esp32", "web", "calibers.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(rows)} calibers -> {out}")


if __name__ == "__main__":
    main()
