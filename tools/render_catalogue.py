"""
Dump a schematic SVG illustration for every watch in the reference catalogue.

    python -m tools.render_catalogue [out_dir] [--png] [--size N]

Default out_dir is  images/catalogue/  next to the package. Files are named
<brand>_<reference or model>.svg. With --png, also writes a PNG beside each
SVG (needs PySide6). Nothing here is committed by default -- run it when you
want the files on disk; the app renders the same illustrations on demand.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watchgrapher import watchart                       # noqa: E402
from watchgrapher.catalog import CATALOG                 # noqa: E402


def _slug(*bits):
    s = "_".join(b for b in bits if b)
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    return s or "watch"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="render_catalogue")
    ap.add_argument("out_dir", nargs="?", default=None)
    ap.add_argument("--png", action="store_true", help="also write a PNG per watch")
    ap.add_argument("--size", type=int, default=480)
    a = ap.parse_args(argv)

    out = a.out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "images", "catalogue")
    os.makedirs(out, exist_ok=True)

    renderer = None
    if a.png:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtGui, QtCore, QtSvg          # noqa: F401
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        renderer = (QtGui, QtCore, QtSvg)

    n = 0
    for e in CATALOG:
        svg = watchart.watch_svg_for(e, size=a.size)
        name = _slug(e.brand, e.reference or e.model)
        with open(os.path.join(out, name + ".svg"), "w", encoding="utf-8") as fh:
            fh.write(svg)
        if renderer:
            QtGui, QtCore, QtSvg = renderer
            r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
            img = QtGui.QImage(a.size, a.size, QtGui.QImage.Format_ARGB32)
            img.fill(QtGui.QColor("white"))
            p = QtGui.QPainter(img)
            r.render(p)
            p.end()
            img.save(os.path.join(out, name + ".png"))
        n += 1

    print(f"wrote {n} illustrations to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
