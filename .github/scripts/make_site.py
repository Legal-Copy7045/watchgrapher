#!/usr/bin/env python3
"""Assemble the GitHub Pages site: the browser installer for the ESP32-S3
standalone version, its firmware and manifests.

Usage: make_site.py <output dir> <variant>=<platformio build dir> ...

Variants:
  n16r8 -> manifest.json and firmware/ at the site root (16 MB flash, 8 MB PSRAM)
  n8r8  -> n8r8/manifest.json and n8r8/firmware/ (8 MB flash, 8 MB PSRAM)

Each manifest serves two purposes:
  * ESP Web Tools reads `builds[].parts` to flash a new board from the browser.
  * The device's own Settings page reads `version` and `builds[].ota` to offer
    an over-the-air update.
"""

import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import urllib.request

REPO = Path(__file__).resolve().parents[2]
# The browser installer (ESP Web Tools) is published with the site rather than
# loaded from a CDN, so the page that flashes boards (and sees the Wi-Fi
# password) runs only code checked here. To upgrade: change both values; the
# integrity is npm's "dist.integrity" for that version.
ESP_WEB_TOOLS_VERSION = "10.4.0"
ESP_WEB_TOOLS_INTEGRITY = (
    "sha512-3pwkeFFm5Fj7UQo8SJNYK5RXrtNCpq6X9QoI6bMT4GBZWgrJqjn0YvM9ihG74BtMoSFYXfmDtkehuxe50PTMPQ=="
)
RELEASES = "https://github.com/Legal-Copy7045/watchgrapher/releases"
VARIANTS = {
    "n16r8": ("WatchGrapher (16 MB flash)", ""),
    "n8r8": ("WatchGrapher (8 MB flash)", "n8r8/"),
}


def build_variant(out: Path, variant: str, build_dir: Path, version: str) -> None:
    title, sub = VARIANTS[variant]
    factory, ota = build_dir / "firmware.factory.bin", build_dir / "firmware.bin"
    for f in (factory, ota):
        if not f.is_file():
            sys.exit(f"missing build output: {f}")
    base = out / sub
    (base / "firmware").mkdir(parents=True, exist_ok=True)
    slug = f"watchgrapher-{variant}"
    shutil.copy(factory, base / "firmware" / f"{slug}.factory.bin")
    shutil.copy(ota, base / "firmware" / f"{slug}.ota.bin")
    manifest = {
        "name": title,
        "version": version,
        "new_install_prompt_erase": True,
        "builds": [
            {
                "chipFamily": "ESP32-S3",
                "parts": [{"path": f"firmware/{slug}.factory.bin", "offset": 0}],
                "ota": {
                    "path": f"firmware/{slug}.ota.bin",
                    "md5": hashlib.md5(ota.read_bytes()).hexdigest(),
                    "summary": f"{title} {version}",
                    "release_url": f"{RELEASES}/tag/esp32-v{version}",
                },
            }
        ],
    }
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{title} {version} -> {base}")


def vendor_esp_web_tools(out: Path) -> None:
    """Download ESP Web Tools from npm, check its hash, and copy its browser build to out/esp-web-tools/."""
    url = f"https://registry.npmjs.org/esp-web-tools/-/esp-web-tools-{ESP_WEB_TOOLS_VERSION}.tgz"
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    algo, _, expected = ESP_WEB_TOOLS_INTEGRITY.partition("-")
    actual = base64.b64encode(hashlib.new(algo, data).digest()).decode()
    if actual != expected:
        sys.exit(f"esp-web-tools {ESP_WEB_TOOLS_VERSION}: hash mismatch ({algo}-{actual})")
    dest = out / "esp-web-tools"
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            if not member.isfile():
                continue
            if name.startswith("package/dist/web/") and "/" not in name.removeprefix("package/dist/web/"):
                target = dest / Path(name).name
            elif name == "package/LICENSE":
                target = dest / "LICENSE"
            else:
                continue
            target.write_bytes(tar.extractfile(member).read())
    if not (dest / "install-button.js").is_file():
        sys.exit("esp-web-tools: install-button.js missing from the package")
    print(f"ESP Web Tools {ESP_WEB_TOOLS_VERSION} -> {dest}")


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    version = (REPO / "esp32" / "VERSION").read_text().strip()
    for spec in sys.argv[2:]:
        variant, build_dir = spec.split("=", 1)
        build_variant(out, variant, Path(build_dir), version)
    page = (REPO / "esp32" / "installer" / "index.html").read_text().replace("{{VERSION}}", version)
    (out / "index.html").write_text(page)
    shutil.copy(REPO / "esp32" / "installer" / "icon.svg", out / "icon.svg")
    vendor_esp_web_tools(out)
    (out / ".nojekyll").write_text("")


if __name__ == "__main__":
    main()
