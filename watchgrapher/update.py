"""
Check GitHub for a newer version, and -- for a git checkout -- apply it.

The app calls ``check()`` on a background thread at start-up. If the folder is
a git clone (the normal ``run.bat`` install), ``apply()`` does a fast-forward
pull, reinstalls dependencies only if ``requirements.txt`` changed, and the app
restarts itself. A zip install just gets pointed at the download page.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass

REPO = "Legal-Copy7045/watchgrapher"
RELEASES_URL = f"https://github.com/{REPO}"
_RAW_INIT = f"https://raw.githubusercontent.com/{REPO}/main/watchgrapher/__init__.py"

# Hide the console window that subprocess would otherwise flash on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class UpdateInfo:
    available: bool = False
    current: str = ""
    latest: str = ""
    method: str = ""        # "git" | "manual"
    behind: int = 0
    detail: str = ""


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_git_checkout() -> bool:
    return os.path.isdir(os.path.join(_root(), ".git"))


def _ver(s: str):
    return tuple(int(x) for x in re.findall(r"\d+", s or "0")[:3])


def _find_version(text: str) -> str:
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)', text or "")
    return m.group(1) if m else ""


def _git(*args, timeout=20):
    return subprocess.run(["git", "-C", _root(), *args], capture_output=True,
                          text=True, timeout=timeout, creationflags=_NO_WINDOW)


def check(timeout: float = 6.0) -> "UpdateInfo | None":
    """Return an UpdateInfo, or None if the check could not run (offline etc.)."""
    from . import __version__
    cur = __version__
    try:
        if is_git_checkout():
            _git("fetch", "--quiet", "origin", "main", timeout=timeout + 15)
            cnt = _git("rev-list", "--count", "HEAD..origin/main")
            behind = int((cnt.stdout or "0").strip() or "0")
            show = _git("show", "origin/main:watchgrapher/__init__.py")
            latest = _find_version(show.stdout) or cur
            if behind > 0:
                return UpdateInfo(True, cur, latest, "git", behind,
                                  f"{behind} commit(s) behind the latest on GitHub.")
            return UpdateInfo(False, cur, latest, "git")
        with urllib.request.urlopen(_RAW_INIT, timeout=timeout) as r:
            latest = _find_version(r.read().decode("utf-8", "replace")) or cur
        if _ver(latest) > _ver(cur):
            return UpdateInfo(True, cur, latest, "manual", 0,
                              "A newer version is published on GitHub.")
        return UpdateInfo(False, cur, latest, "manual")
    except Exception:
        return None


def apply() -> "tuple[bool, str]":
    """Fast-forward the checkout and reinstall deps if needed. git installs only."""
    if not is_git_checkout():
        return False, "This is not a git checkout -- update by re-downloading."
    st = _git("status", "--porcelain")
    dirty = [ln for ln in (st.stdout or "").splitlines()
             if ln and not ln.startswith("??")]
    if dirty:
        return (False, "There are local changes to tracked files here, so an "
                "automatic update could clobber them. Update by hand with git.")
    pull = _git("pull", "--ff-only", "origin", "main", timeout=90)
    if pull.returncode != 0:
        return False, "git pull failed:\n\n" + (pull.stderr or pull.stdout or "")
    changed = _git("diff", "--name-only", "HEAD@{1}", "HEAD").stdout or ""
    if "requirements.txt" in changed:
        req = os.path.join(_root(), "requirements.txt")
        pip = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req],
                             capture_output=True, text=True, timeout=900,
                             creationflags=_NO_WINDOW)
        if pip.returncode != 0:
            return True, ("Updated, but installing new dependencies had a problem:\n\n"
                          + (pip.stderr or "")[-1000:])
    return True, (pull.stdout or "Updated.").strip()


def restart():
    """Relaunch the app with the same interpreter and detach."""
    subprocess.Popen([sys.executable, "-m", "watchgrapher"], cwd=_root(),
                     close_fds=True, creationflags=_NO_WINDOW)
