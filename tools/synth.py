"""
Synthetic escapement audio with known ground truth.

Used to validate the analysis chain: if the analyzer cannot recover a rate,
beat error and amplitude that you deliberately dialled in, it will not
recover them from a real watch either.

Also useful as a bench signal for setting thresholds without a watch present.
"""
import numpy as np


def _burst(fs, freq, decay_ms, n):
    t = np.arange(n) / fs
    return np.sin(2 * np.pi * freq * t) * np.exp(-t / (decay_ms / 1000.0))


def synth_watch(duration=20.0, fs=48000, bph=28800, amplitude=275.0,
                lift_angle=52.0, rate_spd=0.0, beat_error_ms=0.0,
                snr_db=20.0, jitter_us=25.0, seed=0, asymmetry=0.0,
                echo_ms=0.0, echo_level=0.0, wheel_error_ms=0.0,
                wheel_period_beats=30.0):
    """
    Build a signal for a watch with the stated behaviour.

    rate_spd       seconds per day the watch gains (negative = loses)
    beat_error_ms  the on-screen gap between the two trace lines, the same
                   definition the analyzer reports
    amplitude      peak balance swing in degrees, sets the 1st-to-3rd spacing
    asymmetry      0..1. How differently the tick and the tock distribute
                   loudness across their three sub-noises. Real escapements
                   are asymmetric here -- the entry and exit pallet stones
                   are not interchangeable -- and at high values the loudest
                   noise in a tick is a DIFFERENT one of the three than in a
                   tock. Any peak-anchored analyzer that does not account for
                   this will read a beat error that is not there.
    echo_ms        a spurious fourth noise (case resonance, rotor, reflection)
                   this many ms after the drop, at echo_level relative loudness
    wheel_error_ms a sinusoidal timing modulation of this half-amplitude,
                   repeating every wheel_period_beats. This is what an
                   eccentric escape wheel or a bent pivot actually does: the
                   mean rate barely moves, but the watch runs fast and slow in
                   a cycle matching the rotation of the guilty part. Default
                   period of 30 beats is one revolution of a 15-tooth escape
                   wheel.
    """
    rng = np.random.default_rng(seed)
    n = int(duration * fs)
    x = np.zeros(n)

    nominal_period = 3600.0 / bph
    period = nominal_period / (1.0 + rate_spd / 86400.0)

    # dt between the unlocking and the drop, from the harmonic relation.
    t_osc = 2.0 * 3600.0 / bph
    dt = (t_osc / np.pi) * np.arcsin(np.clip((lift_angle / 2.0) / amplitude, -1, 1))

    c = (beat_error_ms / 1000.0) / 2.0   # BE = |mean_even - mean_odd| = 2c

    nbeats = int(duration / period) - 2
    for k in range(nbeats):
        wob = 0.0
        if wheel_error_ms:
            wob = (wheel_error_ms / 1000.0) * np.sin(2 * np.pi * k / wheel_period_beats)
        t0 = (0.05 + k * period + ((-1) ** k) * c + wob
              + rng.normal(0, jitter_us / 1e6))
        # Entry and exit pallets do not sound identical.
        tone = 4200.0 if k % 2 == 0 else 4700.0
        # 1: unlocking (quiet)  2: impulse  3: drop onto the opposite stone (loud)
        a = asymmetry if k % 2 else -asymmetry
        # Clamped: a real escapement noise cannot have negative loudness.
        events = [(0.0, max(0.12, 0.35 + 0.10 * a), tone * 1.15, 0.35),
                  (dt * 0.55, max(0.12, 0.55 + 0.55 * a), tone, 0.45),
                  (dt, max(0.45, 1.00 - 0.45 * a), tone * 0.92, 0.60)]
        if echo_level > 0:
            events.append((dt + echo_ms / 1000.0, echo_level, tone * 0.8, 0.5))
        for off, amp, f, dec in events:
            s = int((t0 + off) * fs)
            ln = int(fs * 0.004)
            if s < 0 or s + ln >= n:
                continue
            x[s:s + ln] += amp * _burst(fs, f * (1 + rng.normal(0, 0.01)), dec, ln)

    sig_rms = np.sqrt(np.mean(x ** 2)) + 1e-12
    noise = rng.normal(0, sig_rms / (10 ** (snr_db / 20.0)), n)
    # A little low-frequency rumble, as any real room has.
    rumble = np.cumsum(rng.normal(0, 1, n))
    rumble = rumble / (np.std(rumble) + 1e-12) * sig_rms * 0.8
    x = x + noise + rumble
    return (x / (np.max(np.abs(x)) + 1e-12) * 0.7).astype(np.float32), fs
