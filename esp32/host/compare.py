"""
Check the C++ DSP core (esp32/lib/wgdsp) against the Python analyzer.

Synthesises a grid of watches with tools/synth.py, runs each through
watchgrapher.analysis.analyze and through the host build of the firmware's
analyzer (wgcli), and compares both against the ground truth and each other.

    cmake -S esp32/host -B build/host && cmake --build build/host
    python esp32/host/compare.py build/host/wgcli
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import numpy as np  # noqa: E402
from synth import synth_watch  # noqa: E402
from watchgrapher import analysis  # noqa: E402

CASES = [
    # name, synth kwargs
    ("baseline 28800", dict()),
    ("gaining +12", dict(rate_spd=12.0)),
    ("losing -35", dict(rate_spd=-35.0)),
    ("beat error 1.2", dict(beat_error_ms=1.2)),
    ("beat error 3.5", dict(beat_error_ms=3.5, rate_spd=4.0)),
    ("low amp 190", dict(amplitude=190.0)),
    ("high amp 315", dict(amplitude=315.0)),
    ("21600 lift 52", dict(bph=21600, amplitude=260.0)),
    ("18000 lift 44", dict(bph=18000, amplitude=240.0, lift_angle=44.0)),
    ("36000", dict(bph=36000, amplitude=280.0, rate_spd=-6.0)),
    ("asymmetric 0.6", dict(asymmetry=0.6, beat_error_ms=0.4)),
    ("asymmetric 0.9", dict(asymmetry=0.9)),
    ("echo 4 ms", dict(echo_ms=4.0, echo_level=0.5)),
    ("noisy 10 dB", dict(snr_db=10.0)),
    ("very noisy 4 dB", dict(snr_db=4.0, rate_spd=3.0)),
    ("wheel error", dict(wheel_error_ms=0.3)),
]
FIELDS = ("detected_bph", "rate", "beat_error", "amplitude")
# Agreement the port must hold with the Python on every case, per field.
TOL = {"rate": 0.5, "beat_error": 0.15, "amplitude": 3.0}


def main():
    cli = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "host", "wgcli")
    secs = float(os.environ.get("WG_SECONDS", "12"))
    fails = 0
    hdr = f"{'case':18} {'field':12} {'truth':>9} {'python':>9} {'c++':>9} {'diff':>7}"
    print(hdr)
    print("-" * len(hdr))
    with tempfile.TemporaryDirectory() as td:
        for name, kw in CASES:
            kw = dict(kw)
            kw.setdefault("amplitude", 275.0)
            kw.setdefault("lift_angle", 52.0)
            kw.setdefault("bph", 28800)
            x, fs = synth_watch(duration=secs, seed=7, **kw)
            path = os.path.join(td, "x.f32")
            x.astype("<f4").tofile(path)

            cfg = analysis.AnalyzerConfig(lift_angle=kw["lift_angle"])
            py = analysis.analyze(x, fs, cfg)
            out = subprocess.run([cli, path, str(fs), str(kw["lift_angle"])],
                                 capture_output=True, text=True, check=True).stdout
            cc = json.loads(out)
            truth = {"detected_bph": kw["bph"], "rate": kw.get("rate_spd", 0.0),
                     "beat_error": kw.get("beat_error_ms", 0.0),
                     "amplitude": kw["amplitude"]}
            for f in FIELDS:
                pv = getattr(py, f)
                cv = cc.get(f)
                pv = float("nan") if pv is None else float(pv)
                cv = float("nan") if cv is None else float(cv)
                d = cv - pv
                bad = False
                if f == "detected_bph":
                    bad = int(cv) != int(pv)
                elif np.isfinite(pv) != np.isfinite(cv):
                    bad = True
                elif np.isfinite(d) and abs(d) > TOL[f]:
                    bad = True
                fails += bad
                print(f"{name:18} {f:12} {truth[f]:9.2f} {pv:9.3f} {cv:9.3f} "
                      f"{d:7.3f}{'  <-- FAIL' if bad else ''}")
            print(f"{'':18} {'c++ ms':12} {cc['ms_total']:9.1f}   msg: {cc['message'][:60]}")
    print(f"\n{fails} disagreement(s) beyond tolerance {TOL}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
