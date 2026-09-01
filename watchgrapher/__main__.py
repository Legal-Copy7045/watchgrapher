"""
Entry point.

    python -m watchgrapher                       launch the GUI
    python -m watchgrapher --devices             list input devices and exit
    python -m watchgrapher --wav f.wav ...       analyze a recording, no GUI
    python -m watchgrapher --listen 30 ...       capture and report, no GUI
    python -m watchgrapher --selftest            run the synthetic validation
"""

from __future__ import annotations

import argparse
import sys

import numpy as np


def _report(m, cal_key=None):
    from .calibers import CALIBERS
    print()
    nb = f"  rate vs {m.nominal_bph}" if m.nominal_bph != m.detected_bph else ""
    print(f"  Beat rate    {m.detected_bph} bph   (raw {m.raw_bph:.1f}){nb}")
    print(f"  Rate         {m.rate:+.1f} s/day")
    print(f"  Amplitude    {'n/a' if m.amplitude != m.amplitude else f'{m.amplitude:.0f} deg'}"
          f"   (lift angle {m.lift_angle:.0f})")
    print(f"  Beat error   {'n/a' if m.beat_error != m.beat_error else f'{m.beat_error:.2f} ms'}")
    print(f"  Beats {m.beats}   SNR {m.snr_db:.0f} dB   match {m.quality:.2f}   {m.duration:.1f}s")
    if m.message and m.message != "OK":
        print(f"  Note: {m.message}")

    if cal_key and cal_key in CALIBERS:
        from . import advisor
        cal = CALIBERS[cal_key]
        r = advisor.Reading("Dial up", m.rate, m.amplitude, m.beat_error)
        print()
        print(advisor.workflow_summary(cal))
        for f in advisor.diagnose(cal, [r], detected_bph=m.detected_bph,
                                  quality=m.quality,
                                  amplitude_spread=m.amplitude_spread):
            print(f"[{f.severity.upper()}] {f.title}")
            for line in _wrap(f.detail, 76):
                print(f"    {line}")
            print()


def diagnose_signal(data, fs, cfg):
    """Dump everything the analyzer sees, so two instruments can be compared."""
    import numpy as np
    from . import dsp
    from .analysis import analyze, amplitude_from_dt

    m = analyze(data, fs, cfg)
    x = np.asarray(data, float).ravel()
    x = x - x.mean()
    xf = dsp.bandpass(x, fs, cfg.band_lo, cfg.band_hi)
    env = dsp.envelope(xf, fs, cfg.env_win_ms)
    per = dsp.verify_period(env, fs, dsp.estimate_beat_period(env, fs))
    beats = dsp.refine_beats(xf, fs, dsp.detect_beats(env, fs, per), per)
    nominal = m.nominal_bph or m.detected_bph
    imp = dsp.measure_impulse(env, fs, beats, nominal, cfg.lift_angle, cfg.sub_threshold)

    print("\n=== SIGNAL =========================================")
    print(f"  duration {len(x)/fs:.1f}s at {fs} Hz, {beats.times.size} beats")
    print(f"  SNR {beats.snr_db:.0f} dB, template match {beats.quality:.3f}")
    print(f"  measured {m.detected_bph} bph (raw {m.raw_bph:.1f}), rate computed vs {nominal}")

    print("\n=== BEAT SPACING ===================================")
    iv = np.diff(beats.times) * 1000.0
    if iv.size > 8:
        t1, t2 = np.median(iv[0::2]), np.median(iv[1::2])
        print(f"  alternating half-periods: {t1:.3f} / {t2:.3f} ms")
        print(f"  |T1-T2|     = {abs(t1-t2):.3f} ms")
        print(f"  |T1-T2|/2   = {abs(t1-t2)/2:.3f} ms   <- reported beat error convention")
    print(f"  beat error reported: {m.beat_error:.3f} ms")

    print("\n=== TICK/TOCK ANCHOR ===============================")
    par = np.arange(beats.times.size) % 2
    if imp.p1_off is not None:
        fin = np.isfinite(imp.p1_off)
        for p_ in (0, 1):
            sel = fin & (par == p_)
            if sel.sum():
                v = imp.p1_off[sel] / fs * 1000.0
                print(f"  {'tick' if p_==0 else 'tock'}: n={sel.sum():>4}  unlock offset "
                      f"{np.median(v):+7.3f} ms  spread {np.std(v):.3f}")
    print(f"  offset measured : {m.parity_offset_seen:+.3f} ms")
    print(f"  offset applied  : {m.parity_correction:+.3f} ms"
          f"{'   (below threshold, left alone)' if m.parity_correction == 0 else ''}")
    print("  If the two apps disagree on beat error, rerun with --no-parity-fix")
    print("  and see which number that produces.")

    print("\n=== IMPULSE / AMPLITUDE ============================")
    d = imp.dt[np.isfinite(imp.dt)]
    if d.size:
        print(f"  usable beats {d.size}/{beats.times.size}")
        print(f"  1st-to-3rd noise: median {np.median(d)*1000:.3f} ms  "
              f"IQR {(np.percentile(d,75)-np.percentile(d,25))*1000:.3f} ms")
    print(f"  noises per beat : {3 + imp.extra_peaks:.2f}  (a lever escapement makes 3)")
    print(f"  lift angle used : {cfg.lift_angle:.1f} deg -> amplitude {m.amplitude:.1f}")
    if d.size:
        print("\n  Amplitude if the lift angle were something else:")
        print("    " + "  ".join(f"{L:>5.0f}" for L in (40, 42, 44, 46, 48, 50, 52, 54, 56)))
        print("    " + "  ".join(
            f"{amplitude_from_dt(float(np.median(d)), nominal, L):>5.0f}"
            for L in (40, 42, 44, 46, 48, 50, 52, 54, 56)))
        print("\n  If another tool disagrees on amplitude, check its lift angle against")
        print("  this row before assuming either measurement is wrong.")
    print()
    return m


def _wrap(text, width):
    import textwrap
    return textwrap.wrap(text, width) or [""]


def selftest():
    """Validate the chain against synthetic signals with known ground truth."""
    from .analysis import analyze, AnalyzerConfig
    sys.path.insert(0, ".")
    try:
        from tools.synth import synth_watch
    except ImportError:
        print("selftest needs tools/synth.py alongside the package.")
        return 1

    # asym: how differently the tick and tock spread loudness over their three
    # noises. echo: a spurious fourth noise after the drop. Both are ordinary
    # on a real watch and both used to break the analyzer badly -- asymmetry
    # invented multiple ms of beat error, an echo halved the amplitude.
    cases = [
        (28800, 275, 52, 0.0, 0.0, 0.0, 0, 0), (28800, 275, 52, 12.0, 0.4, 0.0, 0, 0),
        (28800, 310, 52, -25.0, 0.9, 0.0, 0, 0), (21600, 250, 53, 8.0, 0.3, 0.0, 0, 0),
        (18000, 240, 44, -40.0, 1.5, 0.0, 0, 0), (36000, 265, 50, 3.0, 0.2, 0.0, 0, 0),
        (25200, 280, 38, -5.0, 0.6, 0.0, 0, 0),
        (28800, 270, 52, 0.0, 0.0, 0.9, 0, 0),      # loudest noise flips tick vs tock
        (28800, 270, 52, 6.0, 0.4, 0.6, 0, 0),
        (28800, 270, 52, 0.0, 1.2, 0.9, 0, 0),
        (28800, 270, 52, 0.0, 0.4, 0.0, 5.6, 0.45),  # spurious 4th noise
        (28800, 200, 52, -9.0, 0.8, 0.6, 9.0, 0.55),  # both at once
    ]
    print(f"{'bph':>6} {'rate':>14} {'beat err':>14} {'amplitude':>14}  result")
    bad = 0
    for bph, amp, lift, rate, be, asym, em, el in cases:
        x, fs = synth_watch(duration=20.0, fs=48000, bph=bph, amplitude=amp,
                            lift_angle=lift, rate_spd=rate, beat_error_ms=be, snr_db=15,
                            asymmetry=asym, echo_ms=em, echo_level=el)
        m = analyze(x, fs, AnalyzerConfig(lift_angle=lift))
        ok = (m.detected_bph == bph and abs(m.rate - rate) < 1.5
              and abs(m.beat_error - be) < 0.25 and abs(m.amplitude - amp) < 15)
        bad += 0 if ok else 1
        tag = ""
        if asym:
            tag += " asym"
        if el:
            tag += " echo"
        print(f"{bph:>6} {rate:>6.1f}/{m.rate:>7.1f} {be:>6.2f}/{m.beat_error:>7.2f} "
              f"{amp:>6.0f}/{m.amplitude:>7.1f}  {'pass' if ok else 'FAIL'}{tag}")
    print("\nAll cases passed." if not bad else f"\n{bad} case(s) failed.")
    return 0 if not bad else 1


def main(argv=None):
    from . import __version__
    ap = argparse.ArgumentParser(prog="watchgrapher", description="Acoustic watch timegrapher")
    ap.add_argument("--version", action="version", version=f"watchgrapher {__version__}")
    ap.add_argument("--devices", action="store_true", help="list audio input devices")
    ap.add_argument("--wav", help="analyze a WAV file instead of the GUI")
    ap.add_argument("--listen", type=float, metavar="SEC",
                    help="capture for SEC seconds and report, no GUI")
    ap.add_argument("--device", type=int, help="input device index for --listen")
    ap.add_argument("--rate", type=int, default=48000, help="sample rate for --listen")
    ap.add_argument("--caliber", help="caliber key, e.g. eta_2824_2 (sets lift angle and advice)")
    ap.add_argument("--lift", type=float, help="lift angle override, degrees")
    ap.add_argument("--bph", type=int, help="force beat rate instead of auto-detecting")
    ap.add_argument("--threshold", type=float, default=0.16, help="sub-noise threshold")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--find-model", metavar="TEXT",
                    help='look a movement up by watch model, e.g. --find-model "air king"')
    ap.add_argument("--diagnose", action="store_true",
                    help="dump full internals for comparing against another tool")
    ap.add_argument("--no-parity-fix", action="store_true",
                    help="disable the tick/tock anchor correction")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    if a.find_model:
        from .catalog import search as search_models
        from .calibers import CALIBERS
        rows = search_models(a.find_model)
        if not rows:
            print(f"No model matches '{a.find_model}'.")
            return 1
        print(f"{'Model':<46} {'Years':<12} {'Movement':<34} Lift  Caliber key")
        for m in rows:
            c = CALIBERS[m.caliber_key]
            flag = "" if m.confidence == "sure" else "  (unconfirmed)"
            print(f"{m.label[:45]:<46} {(m.years or ''):<12} "
                  f"{(c.brand + ' ' + c.name)[:33]:<34} {c.lift_angle:>4.0f}  "
                  f"{m.caliber_key}{flag}")
        return 0

    if a.devices:
        from .audio import list_input_devices, HAVE_SD
        if not HAVE_SD:
            print("sounddevice not installed.  pip install sounddevice")
            return 1
        for i, name, ch, sr, api in list_input_devices():
            print(f"  {i:>3}  [{api}] {name}  ({ch} ch, {sr} Hz)")
        return 0

    from .analysis import analyze, AnalyzerConfig
    from .calibers import CALIBERS

    lift = a.lift
    if a.caliber:
        if a.caliber not in CALIBERS:
            print(f"Unknown caliber '{a.caliber}'. Known keys:")
            for k in sorted(CALIBERS):
                print("   ", k)
            return 1
        c = CALIBERS[a.caliber]
        lift = lift if lift is not None else c.lift_angle
    cfg = AnalyzerConfig(lift_angle=lift if lift is not None else 52.0,
                         forced_bph=a.bph, sub_threshold=a.threshold,
                         no_parity_fix=a.no_parity_fix)

    if a.wav:
        from .audio import load_wav
        data, fs = load_wav(a.wav)
        if a.diagnose:
            diagnose_signal(data, fs, cfg)
            return 0
        _report(analyze(data, fs, cfg), a.caliber)
        return 0

    if a.listen:
        import time
        from .audio import Recorder
        rec = Recorder(device=a.device, samplerate=a.rate,
                       buffer_seconds=a.listen + 5)
        rec.start()
        print(f"Listening for {a.listen:.0f}s ...", flush=True)
        time.sleep(a.listen)
        data = rec.read(a.listen)
        rec.stop()
        if a.diagnose:
            diagnose_signal(data, rec.samplerate, cfg)
            return 0
        _report(analyze(data, rec.samplerate, cfg), a.caliber)
        return 0

    from .ui import main as gui
    gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
