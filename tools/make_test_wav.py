"""
Write a set of test WAV files with known ground truth.

    python tools/make_test_wav.py [output_dir]

Each file's real values are in its name, so you can load it with
"Analyze a WAV file" and check the readouts against the filename.
"""
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.synth import synth_watch

CASES = [
    ("healthy_eta2824",   dict(bph=28800, amplitude=290, lift_angle=50, rate_spd=2,   beat_error_ms=0.15)),
    ("needs_regulating",  dict(bph=28800, amplitude=282, lift_angle=50, rate_spd=42,  beat_error_ms=0.20)),
    ("bad_beat_error",    dict(bph=28800, amplitude=275, lift_angle=50, rate_spd=-6,  beat_error_ms=1.60)),
    ("low_amplitude",     dict(bph=28800, amplitude=185, lift_angle=50, rate_spd=-31, beat_error_ms=0.70)),
    ("knocking",          dict(bph=28800, amplitude=338, lift_angle=50, rate_spd=88,  beat_error_ms=0.30)),
    ("seiko_nh35",        dict(bph=21600, amplitude=258, lift_angle=53, rate_spd=-12, beat_error_ms=0.45)),
    ("vintage_6497",      dict(bph=18000, amplitude=245, lift_angle=44, rate_spd=19,  beat_error_ms=0.90)),
    ("omega_coaxial",     dict(bph=25200, amplitude=272, lift_angle=38, rate_spd=-3,  beat_error_ms=0.25)),
    ("zenith_hibeat",     dict(bph=36000, amplitude=262, lift_angle=50, rate_spd=5,   beat_error_ms=0.30)),
    ("noisy_pickup",      dict(bph=28800, amplitude=270, lift_angle=50, rate_spd=8,   beat_error_ms=0.40, snr_db=4)),
]


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "test_wavs"
    os.makedirs(out, exist_ok=True)
    for name, kw in CASES:
        kw.setdefault("snr_db", 18)
        x, fs = synth_watch(duration=35.0, fs=48000, **kw)
        fn = (f"{name}__{kw['bph']}bph_amp{kw['amplitude']:.0f}"
              f"_lift{kw['lift_angle']:.0f}_rate{kw['rate_spd']:+.0f}"
              f"_be{kw['beat_error_ms']:.2f}.wav")
        path = os.path.join(out, fn)
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs)
            w.writeframes(np.clip(x * 32000, -32768, 32767).astype("<i2").tobytes())
        print("wrote", path)
    print(f"\n{len(CASES)} files in {os.path.abspath(out)}")
    print("Set the lift angle to match the filename before reading amplitude.")


if __name__ == "__main__":
    main()
