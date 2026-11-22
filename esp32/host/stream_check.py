"""
Check the device's streaming analysis (wg::Stream) against the whole-window
analysis it replaces, on the comparison cases, and time both.

    python esp32/host/stream_check.py build/host/wgcli

Stream filters each block once with margins and keeps the result; the
window it analyses ends a filter margin (tens of ms) before the newest audio,
so the two see windows a few beats apart at the edges and agree closely
rather than exactly.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compare  # noqa: E402

TOL = {"rate": 0.3, "beat_error": 0.1, "amplitude": 2.0}


def run(cli, path, fs, lift, mode):
    env = dict(os.environ, WG_MODE=mode)
    out = subprocess.run([cli, path, str(fs), str(lift)], capture_output=True, text=True,
                         check=True, env=env).stdout
    return json.loads(out)


def main():
    cli = sys.argv[1] if len(sys.argv) > 1 else os.path.join(compare.ROOT, "build", "host", "wgcli")
    fails = 0
    t_b = t_s = 0.0
    with tempfile.TemporaryDirectory() as td:
        for name, kw in compare.CASES:
            kw = dict(kw)
            kw.setdefault("amplitude", 275.0)
            kw.setdefault("lift_angle", 52.0)
            kw.setdefault("bph", 28800)
            if kw.pop("simrec", False):
                x, fs = compare.simrec(12, kw), 48000
            else:
                x, fs = compare.synth_watch(duration=12, seed=7, **kw)
            path = os.path.join(td, "x.f32")
            x.astype("<f4").tofile(path)
            b = run(cli, path, fs, kw["lift_angle"], "batch")
            s = run(cli, path, fs, kw["lift_angle"], "stream")
            t_b += b["ms_total"]
            t_s += s["ms_total"]
            bad = b["ok"] != s["ok"] or b["detected_bph"] != s["detected_bph"]
            # At 9 dB of white noise both analyzers already misread beat error
            # and amplitude (noise passes for the unlocking noise); only the
            # rate is meaningful there.
            fields = {"rate": TOL["rate"]} if name.startswith("noisy sim") else TOL
            for f, tol in fields.items():
                bv, sv = b.get(f), s.get(f)
                if (bv is None) != (sv is None) or (bv is not None and abs(bv - sv) > tol):
                    bad = True
            fails += bad
            print(f"{name:18} batch rate {b['rate']:8.3f} amp {b['amplitude'] or 0:7.2f} be {b['beat_error'] or 0:6.3f}"
                  f" | stream rate {s['rate']:8.3f} amp {s['amplitude'] or 0:7.2f} be {s['beat_error'] or 0:6.3f}"
                  f"{'  <-- FAIL' if bad else ''}")
    n = len(compare.CASES)
    print(f"\nper analysis on this machine: whole window {t_b / n:.1f} ms, streaming update {t_s / n:.1f} ms")
    print(f"{fails} disagreement(s) beyond {TOL}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
