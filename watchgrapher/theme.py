"""
Colour theme.

The UI reads its colours from this module at import time, so the whole app
picks up whichever palette is active. Switching theme writes the choice to
settings.json and takes effect on the next start -- a live swap would have to
chase 70-odd inline stylesheets and is not worth the fragility.
"""

from __future__ import annotations

import json
import os

_SETTINGS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")

DARK = {
    "BG": "#12151a", "BG2": "#171b22", "PANEL": "#1a1f27", "PANEL2": "#20262f",
    "LINE": "#2a323e", "INK_HI": "#e8eef7", "INK": "#c8d0dc", "MUT": "#8a94a4",
    "MUT2": "#5a6472", "ACCENT": "#4da3ff", "ACCENT2": "#7fb2ff",
    "ON_ACCENT": "#08101c", "GOOD": "#57d38c", "WARN": "#ffb648", "WARN2": "#ff9d4d",
    "BAD": "#ff5d5d", "TICK": "#4da3ff", "TOCK": "#ff9d4d",
    "PLOT_BG": "#12151a", "PLOT_FG": "#8a94a4",
}

LIGHT = {
    "BG": "#eef1f5", "BG2": "#e4e8ee", "PANEL": "#ffffff", "PANEL2": "#f4f6f9",
    "LINE": "#d3dae3", "INK_HI": "#141a22", "INK": "#2b333e", "MUT": "#5f6b7a",
    "MUT2": "#8a95a3", "ACCENT": "#1f6fd0", "ACCENT2": "#3b7fd6",
    "ON_ACCENT": "#ffffff", "GOOD": "#1f8a4c", "WARN": "#b7791f", "WARN2": "#c26a12",
    "BAD": "#c0392b", "TICK": "#1f6fd0", "TOCK": "#c26a12",
    "PLOT_BG": "#ffffff", "PLOT_FG": "#5f6b7a",
}


def _load_mode() -> str:
    try:
        with open(_SETTINGS, encoding="utf-8") as fh:
            return json.load(fh).get("theme", "dark")
    except (OSError, ValueError):
        return "dark"


def save_mode(mode: str):
    try:
        data = {}
        if os.path.exists(_SETTINGS):
            with open(_SETTINGS, encoding="utf-8") as fh:
                data = json.load(fh)
        data["theme"] = mode
        with open(_SETTINGS, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


def _system_is_light() -> bool:
    try:
        from PySide6 import QtWidgets, QtGui
        app = QtWidgets.QApplication.instance()
        if app is None:
            return False
        c = app.palette().color(QtGui.QPalette.Window)
        return c.lightnessF() > 0.5
    except Exception:
        return False


MODE = _load_mode()                      # "dark" | "light" | "system"
_effective = ("light" if MODE == "light"
              else "light" if (MODE == "system" and _system_is_light())
              else "dark")
P = LIGHT if _effective == "light" else DARK
IS_LIGHT = _effective == "light"


def get(name: str) -> str:
    return P.get(name, "#888888")
