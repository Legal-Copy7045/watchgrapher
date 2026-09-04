"""
Turns a set of beat times into the four numbers a timegrapher reports.

Rate         seconds gained or lost per day
Beat error   milliseconds of asymmetry between the tick and the tock
Amplitude    peak swing of the balance from its rest position, in degrees
Beat rate    detected bph, snapped to a standard frequency

Sign convention for rate: positive means the watch is GAINING.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from typing import Optional

import numpy as np

from . import dsp
from .calibers import snap_bph, STANDARD_BPH


# --------------------------------------------------------------------------
# Amplitude
# --------------------------------------------------------------------------

def amplitude_from_dt(dt: float, bph: float, lift_angle: float,
                      exact: bool = True) -> float:
    """
    Amplitude in degrees from the 1st-to-3rd noise interval.

    Exact (harmonic) form, inverting  lift/2 = A*sin(pi*dt/T_osc):

        A = (lift/2) / sin(pi * dt * bph / 7200)

    The formula printed in most references,

        A = 3600 * lift / (pi * dt * bph)

    is the small-angle approximation of the same thing. They agree to about
    0.4% at 270 degrees but the approximation drifts high as amplitude
    climbs, which is exactly where you care -- near the 320+ region where
    you are watching for knocking.
    """
    if dt is None or not np.isfinite(dt) or dt <= 0:
        return float("nan")
    if exact:
        s = np.sin(np.pi * dt * bph / 7200.0)
        if s <= 1e-9:
            return float("nan")
        return float((lift_angle / 2.0) / s)
    return float(3600.0 * lift_angle / (np.pi * dt * bph))


def dt_from_amplitude(amp: float, bph: float, lift_angle: float) -> float:
    """Inverse of the above -- used by the lift-angle solver."""
    t_osc = 2.0 * 3600.0 / bph
    s = np.clip((lift_angle / 2.0) / max(amp, 1e-6), -1.0, 1.0)
    return float((t_osc / np.pi) * np.arcsin(s))


def solve_lift_angle(dt: float, bph: float, known_amplitude: float) -> float:
    """
    If you know the true amplitude by another route -- most practically the
    180-degree trick, where you let the watch run down until a marked
    balance arm appears to stall exactly opposite its rest position -- this
    back-solves the caliber's lift angle.
    """
    return float(2.0 * known_amplitude * np.sin(np.pi * dt * bph / 7200.0))


# --------------------------------------------------------------------------
# Rate and beat error
# --------------------------------------------------------------------------

def fit_rate(times: np.ndarray, period_guess: float, passes: int = 3):
    """
    Least-squares fit of beat time against beat INDEX.

    Indices are assigned by rounding elapsed time over the estimated period
    rather than assuming the detections are consecutive, so a missed or
    spurious beat shifts one point instead of corrupting everything after it.

    Returns (intercept, period, indices, residuals).
    """
    t = np.asarray(times, dtype=float)
    if t.size < 8:
        return 0.0, period_guess, np.array([]), np.array([])

    period = period_guess
    a = t[0]
    idx = np.zeros(t.size)
    resid = np.zeros(t.size)

    for _ in range(passes):
        # Assign beat numbers from SUCCESSIVE intervals, not from elapsed time.
        # Each gap is about one period, so the rounding is near 1 and tolerant
        # of a sloppy period estimate; a missed beat simply rounds to 2. Using
        # elapsed time instead lets a 0.5% period error accumulate until whole
        # beats are misnumbered, which throws the rate off by tens of s/day.
        steps = np.rint(np.diff(t) / period)
        steps = np.clip(steps, 1, 8)
        idx = np.concatenate([[0.0], np.cumsum(steps)])
        A = np.vstack([np.ones_like(idx), idx]).T
        sol, *_ = np.linalg.lstsq(A, t, rcond=None)
        a, period = float(sol[0]), float(sol[1])
        resid = t - (a + period * idx)
        # Drop gross outliers (bad detections) and refit once more.
        if resid.size > 20:
            s = 1.4826 * np.median(np.abs(resid - np.median(resid))) + 1e-12
            keep = np.abs(resid - np.median(resid)) < 5 * s
            if keep.sum() > 8 and keep.sum() < resid.size:
                t_k, i_k = t[keep], idx[keep]
                A = np.vstack([np.ones_like(i_k), i_k]).T
                sol, *_ = np.linalg.lstsq(A, t_k, rcond=None)
                a, period = float(sol[0]), float(sol[1])
                resid = t - (a + period * idx)

    return a, period, idx, resid


def rate_spd(measured_period: float, nominal_bph: float) -> float:
    """Seconds per day. Positive = gaining."""
    if measured_period <= 0:
        return float("nan")
    nominal_period = 3600.0 / nominal_bph
    return float((nominal_period / measured_period - 1.0) * 86400.0)


def beat_error_ms(idx: np.ndarray, resid: np.ndarray) -> float:
    """
    With half-periods T1 and T2 (T1 + T2 = one full oscillation), beat error
    is |T1 - T2| / 2. Fitting t_i = a + b*i + (-1)^i * c makes that simply
    the gap between the mean residual of the even beats and the odd beats.
    """
    if idx.size < 8:
        return float("nan")
    even = resid[idx % 2 == 0]
    odd = resid[idx % 2 == 1]
    if even.size < 3 or odd.size < 3:
        return float("nan")
    return float(abs(np.mean(even) - np.mean(odd)) * 1000.0)


# --------------------------------------------------------------------------
# Full measurement
# --------------------------------------------------------------------------

@dataclass
class Measurement:
    ok: bool = False
    message: str = ""
    detected_bph: Optional[int] = None    # what the audio actually measured
    nominal_bph: Optional[int] = None     # what rate was computed against
    raw_bph: float = float("nan")
    rate: float = float("nan")            # s/day, + = gaining
    rate_ci: float = float("nan")         # 95% confidence half-width on rate, s/day
    beat_error: float = float("nan")      # ms
    amplitude: float = float("nan")       # degrees
    amplitude_spread: float = float("nan")  # IQR of per-beat amplitude, degrees
    beats: int = 0
    duration: float = 0.0
    snr_db: float = 0.0
    quality: float = 0.0                  # 0..1, template correlation
    lift_angle: float = 52.0
    # Diagnostics / plotting
    times: np.ndarray = field(default_factory=lambda: np.array([]))
    index: np.ndarray = field(default_factory=lambda: np.array([]))
    resid: np.ndarray = field(default_factory=lambda: np.array([]))
    mean_shape: Optional[np.ndarray] = None
    shape_fs: int = 0
    shape_pre: int = 0
    p1_idx: float = 0.0
    p3_idx: float = 0.0
    # One recent complete beat, band-passed, for the live-beat view.
    beat_wave: Optional[np.ndarray] = None
    beat_wave_fs: int = 0
    beat_wave_pre: int = 0                # samples before the beat anchor
    amp_samples: np.ndarray = field(default_factory=lambda: np.array([]))  # per-beat amplitude, deg
    inst_rate_t: np.ndarray = field(default_factory=lambda: np.array([]))  # s, time within capture
    inst_rate: np.ndarray = field(default_factory=lambda: np.array([]))    # s/day, ~1 s smoothed
    spectrum_f: np.ndarray = field(default_factory=lambda: np.array([]))   # Hz
    spectrum_db: np.ndarray = field(default_factory=lambda: np.array([]))  # dB, peak = 0
    dt_mean: float = float("nan")         # seconds, 1st-to-3rd noise
    parity_correction: float = 0.0        # ms of tick/tock anchor offset removed
    parity_offset_seen: float = 0.0       # ms of offset measured, applied or not
    extra_peaks: float = 0.0              # noises per beat beyond the expected three
    valid_frac: float = 0.0               # fraction of beats yielding a usable interval


@dataclass
class AnalyzerConfig:
    band_lo: float = 1500.0
    band_hi: float = 12000.0
    env_win_ms: float = 0.35
    sub_threshold: float = 0.16      # relative height for sub-noise peaks
    lift_angle: float = 52.0
    forced_bph: Optional[int] = None  # None = auto-detect
    exact_amplitude: bool = True
    no_parity_fix: bool = False       # disable the tick/tock anchor correction


def allan_deviation(t_s, y_spd, min_points: int = 32):
    """
    Overlapping Allan deviation of a rate series.

    `t_s` are sample times in seconds (irregular spacing is fine) and `y_spd`
    the rate in s/day at each. The series is resampled onto a uniform grid at
    its median spacing, then ADEV is computed at octave-spaced averaging
    times tau.

    Returns (tau_s, adev_spd), both in the input's units.

    ADEV(tau) is the rate scatter left after averaging for tau seconds.
    A curve that keeps falling roughly as tau**-0.5 means the reading is
    white-noise limited and a longer capture tightens it; a curve that
    flattens or turns back up means the rate itself is wandering and no
    capture length pins it down past that floor.
    """
    t = np.asarray(t_s, dtype=float)
    y = np.asarray(y_spd, dtype=float)
    good = np.isfinite(t) & np.isfinite(y)
    t, y = t[good], y[good]
    if t.size < min_points:
        return np.array([]), np.array([])
    order = np.argsort(t)
    t, y = t[order], y[order]
    dt = float(np.median(np.diff(t)))
    if not np.isfinite(dt) or dt <= 0:
        return np.array([]), np.array([])
    span = float(t[-1] - t[0])
    # Cap the uniform grid so a tiny median spacing (or a very long run) cannot
    # blow the array up. Above the cap the grid is coarsened to fit.
    max_grid = 200_000
    if span / dt > max_grid:
        dt = span / max_grid
    grid = np.arange(t[0], t[-1] + dt * 0.5, dt)
    yu = np.interp(grid, t, y)
    n = yu.size
    if n < min_points:
        return np.array([]), np.array([])
    csum = np.concatenate([[0.0], np.cumsum(yu)])
    taus, adev = [], []
    m = 1
    while m <= (n - 1) // 3:
        block = (csum[m:] - csum[:-m]) / m          # running mean, window m
        d = block[m:] - block[:-m]                  # successive tau-averages
        if d.size < 2:
            break
        taus.append(m * dt)
        adev.append(float(np.sqrt(np.mean(d ** 2) / 2.0)))
        m *= 2
    return np.asarray(taus), np.asarray(adev)


def _coarse_spectrum(x: np.ndarray, fs: int, bins: int = 360):
    """
    A log-binned magnitude spectrum of the raw signal, for the diagnostics
    view: where the escapement energy sits, and whether the rotor or a case
    resonance is putting energy somewhere the filter is not looking. Peak
    normalised to 0 dB. Returns (freqs_hz, level_db).
    """
    try:
        nfft = 1 << 14
        seg = x[:nfft] if x.size >= nfft else np.pad(x, (0, nfft - x.size))
        mag = np.abs(np.fft.rfft(seg * np.hanning(nfft)))
        fr = np.fft.rfftfreq(nfft, 1.0 / fs)
        keep = (fr >= 20.0) & (fr <= fs / 2.0)
        fr, mag = fr[keep], mag[keep]
        if fr.size < 8:
            return np.array([]), np.array([])
        edges = np.logspace(np.log10(fr[0]), np.log10(fr[-1]), bins + 1)
        idx = np.clip(np.digitize(fr, edges) - 1, 0, bins - 1)
        binned = np.zeros(bins)
        np.maximum.at(binned, idx, mag)          # peak-hold within each bin
        centres = np.sqrt(edges[:-1] * edges[1:])
        ok = binned > 0
        db = 20.0 * np.log10(binned[ok] / (binned[ok].max() + 1e-12) + 1e-9)
        return centres[ok], db
    except Exception:
        return np.array([]), np.array([])


def analyze(samples: np.ndarray, fs: int, cfg: AnalyzerConfig) -> Measurement:
    """Run the full chain on one block of audio."""
    m = Measurement(lift_angle=cfg.lift_angle)
    x = np.asarray(samples, dtype=np.float64).ravel()
    m.duration = len(x) / fs

    if len(x) < fs // 2:
        m.message = "Not enough audio yet."
        return m
    if np.max(np.abs(x)) < 1e-5:
        m.message = "Signal is silent. Check the microphone input and gain."
        return m

    x = x - np.mean(x)
    m.spectrum_f, m.spectrum_db = _coarse_spectrum(x, fs)
    xf = dsp.bandpass(x, fs, cfg.band_lo, cfg.band_hi)
    env = dsp.envelope(xf, fs, cfg.env_win_ms)

    period = dsp.estimate_beat_period(env, fs)
    if period is None or period <= 0:
        m.message = "No periodic escapement signal found. Reposition the watch on the pickup."
        return m

    period = dsp.verify_period(env, fs, period)
    m.raw_bph = 3600.0 / period

    coarse = dsp.detect_beats(env, fs, period)
    if coarse.size < 12:
        m.message = f"Only {coarse.size} beats detected. Signal is too weak or too noisy."
        return m

    beats = dsp.refine_beats(xf, fs, coarse, period)
    m.snr_db = beats.snr_db
    m.quality = beats.quality
    m.beats = int(beats.times.size)

    # Keep one complete beat's band-passed waveform for the live-beat view.
    # The second-to-last beat is safely clear of the window edge.
    if beats.times.size >= 3:
        anchor = int(round(float(beats.times[-2]) * fs))
        pre, post = int(fs * 0.006), int(fs * 0.020)
        if 0 <= anchor - pre and anchor + post < xf.size:
            m.beat_wave = xf[anchor - pre: anchor + post].astype(np.float32)
            m.beat_wave_fs = fs
            m.beat_wave_pre = pre

    # Always record what the audio actually measured, independently of any
    # forced value. Overwriting this with the forced figure hides exactly the
    # case that matters: a caliber selection that does not match the watch.
    snapped = snap_bph(m.raw_bph, tol_frac=0.02)
    m.detected_bph = snapped if snapped else int(round(m.raw_bph))
    nominal = int(cfg.forced_bph) if cfg.forced_bph else m.detected_bph
    m.nominal_bph = nominal
    if cfg.forced_bph and m.detected_bph != nominal:
        m.message = (f"Measured {m.detected_bph} bph but rate is being computed against "
                     f"{nominal} bph. The rate figure is meaningless until these agree -- "
                     f"check the caliber selection, or switch beat rate to Auto-detect.")

    # Measure the impulse interval first -- it also yields a physically
    # anchored reference for the parity correction below.
    imp = dsp.measure_impulse(env, fs, beats, nominal, cfg.lift_angle, cfg.sub_threshold)

    # Parity anchor correction.
    #
    # The coarse detector locks onto whichever sub-noise is loudest, and that
    # need not be the same one for a tick as for a tock -- entry and exit
    # pallet stones do not sound alike. Any such difference is a constant
    # offset applied to every other beat, which is precisely the signature of
    # beat error, so an uncorrected analyzer will report several milliseconds
    # of beat error on a watch that is perfectly in beat.
    #
    # The unlocking noise is the SAME physical event on both half swings, so
    # the difference between the mean unlocking offsets of the even and odd
    # beats is the anchor error and nothing else. Correcting by it leaves real
    # beat error -- which lives in when the beats occur, not in the shape of
    # each one -- completely intact.
    times = np.asarray(beats.times, dtype=float).copy()
    par = np.arange(times.size) % 2
    if imp.p1_off is not None:
        fin = np.isfinite(imp.p1_off)
        ev = imp.p1_off[fin & (par == 0)]
        od = imp.p1_off[fin & (par == 1)]
        if ev.size >= 5 and od.size >= 5:
            d = (np.median(od) - np.median(ev)) / fs
            # Never "correct" by more than half a beat; that would be a
            # detection failure, not an anchor offset.
            # Sign: p1_off is the unlocking position relative to the anchor,
            # so a beat whose anchor sits LATER within the group has a more
            # negative p1_off. Adding the difference re-references the odd
            # beats to the same point in the group as the even ones. Getting
            # this backwards doubles the offset instead of removing it.
            #
            # Gate it hard. An anchor flip is DISCRETE -- the anchor jumps
            # from one sub-noise to another, so a genuine one is worth a large
            # fraction of the impulse interval, several milliseconds. A small
            # offset is measurement noise, and "correcting" by it would
            # subtract real beat error from the reading. Better to leave a
            # small artifact in than to silently null out a real fault.
            dt_ref = np.nanmedian(imp.dt) if np.isfinite(imp.dt).any() else 0.008
            floor = max(0.0012, 0.25 * float(dt_ref))
            m.parity_offset_seen = float(d * 1000.0)
            if floor < abs(d) < 0.4 * period and not cfg.no_parity_fix:
                times[par == 1] += d
                m.parity_correction = float(d * 1000.0)

    a, fitted_period, idx, resid = fit_rate(times, period)
    if idx.size == 0:
        m.message = "Could not fit a stable beat sequence."
        return m

    m.times, m.index, m.resid = times, idx, resid
    m.rate = rate_spd(fitted_period, nominal)
    m.beat_error = beat_error_ms(idx, resid)

    # Instantaneous rate: each beat's own period against nominal, so the fine
    # structure the single averaged number hides is visible -- a slow wave at
    # constant amplitude is poor isochronism or a train fault; a steady line
    # is a healthy escapement. Differentiating is noisy, so smooth to ~1 s.
    if times.size >= 12:
        di = np.diff(idx).astype(float)
        dt_beat = np.diff(times) / np.where(di == 0, np.nan, di)
        nominal_period = 3600.0 / nominal
        inst = (nominal_period / dt_beat - 1.0) * 86400.0
        t_mid = (times[1:] + times[:-1]) / 2.0 - times[0]
        good = np.isfinite(inst)
        inst, t_mid = inst[good], t_mid[good]
        if inst.size >= 8:
            k = max(3, (int(round(1.0 / nominal_period)) | 1))
            if inst.size > k:
                # "valid" (not "same") so the edges are a true running mean
                # rather than a zero-padded one that droops toward 0.
                sm = np.convolve(inst, np.ones(k) / k, mode="valid")
                off = (inst.size - sm.size) // 2
                inst, t_mid = sm, t_mid[off:off + sm.size]
            m.inst_rate_t, m.inst_rate = t_mid, inst

    # 95% confidence half-width on the rate, from the scatter of the beat
    # times about the fitted line. This is what tells you a 20 s window is
    # not enough and a timed run is needed -- precision scales with capture
    # length, so it shrinks as the run gets longer.
    if idx.size >= 8 and fitted_period > 0:
        ix = idx - idx.mean()
        ss = float(np.sum(ix * ix))
        if ss > 0:
            dof = max(1, idx.size - 2)
            sigma = math.sqrt(float(np.sum(resid * resid)) / dof)
            se_period = sigma / math.sqrt(ss)
            nominal_period = 3600.0 / nominal
            drate_dperiod = nominal_period / (fitted_period ** 2) * 86400.0
            m.rate_ci = float(1.96 * drate_dperiod * se_period)

    if imp.valid.sum() >= 8:
        dts = imp.dt[imp.valid]
        # Median is the right statistic here -- a handful of beats will always
        # have a sub-noise masked by room noise, and those land in the tails.
        m.dt_mean = float(np.median(dts))
        m.amplitude = amplitude_from_dt(m.dt_mean, nominal, cfg.lift_angle, cfg.exact_amplitude)
        amps = np.array([amplitude_from_dt(d, nominal, cfg.lift_angle, cfg.exact_amplitude)
                         for d in dts])
        amps = amps[np.isfinite(amps)]
        m.amp_samples = amps
        if amps.size > 4:
            m.amplitude_spread = float(np.percentile(amps, 75) - np.percentile(amps, 25))
    else:
        m.message = ("Rate and beat error are good, but the unlocking noise is not "
                     "clearly resolvable -- amplitude unavailable. Try more gain, "
                     "better acoustic coupling, or a lower sub-noise threshold.")

    m.extra_peaks = imp.extra_peaks
    m.valid_frac = float(imp.valid.sum()) / max(1, imp.valid.size)
    m.mean_shape = imp.mean_shape
    m.shape_fs = imp.shape_fs
    m.shape_pre = imp.shape_pre
    m.p1_idx, m.p3_idx = imp.p1_idx, imp.p3_idx
    if imp.extra_peaks > 0.35 and not m.message:
        m.message = (f"Detecting {3 + imp.extra_peaks:.1f} noises per beat instead of 3. "
                     f"Something beyond the escapement is being picked up -- case "
                     f"resonance, the rotor, or a reflection. Amplitude is the reading "
                     f"at risk; check the beat waveform panel.")

    m.ok = True
    if not m.message:
        m.message = "OK"
    return m


# --------------------------------------------------------------------------
# Trace helper
# --------------------------------------------------------------------------

def trace_points(m: Measurement, nominal_bph: float, window_ms: float = 20.0):
    """
    Classic timegrapher trace: horizontal axis is deviation from a perfect
    beat grid (wrapped into the display window), vertical axis is elapsed
    time. The slope of the resulting lines IS the rate; the vertical gap
    between the two lines IS the beat error.

    Returns (x_tick, y_tick, x_tock, y_tock) with x in ms and y in seconds.
    """
    if m.times.size == 0 or m.index.size == 0:
        e = np.array([])
        return e, e, e, e

    nominal_period = 3600.0 / nominal_bph
    dev = (m.times - m.times[0]) - m.index * nominal_period
    dev_ms = dev * 1000.0
    # Centre it, then wrap into +/- window/2 so a drifting trace re-enters
    # from the other side instead of running off the plot.
    dev_ms = dev_ms - np.median(dev_ms[: max(4, len(dev_ms) // 20)])
    w = window_ms
    wrapped = ((dev_ms + w / 2.0) % w) - w / 2.0
    y = m.times - m.times[0]

    even = m.index % 2 == 0
    return wrapped[even], y[even], wrapped[~even], y[~even]


# --------------------------------------------------------------------------
# Pickup auto-tuning
# --------------------------------------------------------------------------

@dataclass
class ReserveStats:
    """Analytics over a power-reserve run: [(elapsed_s, rate, amplitude, beat_error), ...]."""
    n: int = 0
    hours: float = 0.0
    amp_first: float = float("nan")
    amp_last: float = float("nan")
    amp_per_hour: float = float("nan")     # mean slope over the run, deg/h
    amp_established: bool = False           # amplitude has genuinely fallen (not plateau)
    hours_to_220: float = float("nan")     # extrapolated from the last third of the run
    hours_to_200: float = float("nan")
    kick_deg_per_h: float = float("nan")   # amplitude lost per hour over the first hour
    iso_slope: float = float("nan")        # s/day of rate per +1 deg amplitude
    iso_span: float = float("nan")         # rate change across the amplitude range seen
    iso_fit: tuple = ()                    # (slope, intercept) for a rate-vs-amplitude line
    iso_model: str = "linear"             # "linear" or "quadratic"
    iso_coef: tuple = ()                  # full polynomial coefficients, high order first
    iso_vertex: float = float("nan")      # amplitude of least rate sensitivity (quadratic)
    be_slope: float = float("nan")         # ms beat error per +1 deg amplitude
    iso_n_out: int = 0                     # rate-vs-amplitude points rejected as outliers
    iso_in: tuple = ()                     # (amp[], rate[]) points kept for the fit
    iso_out: tuple = ()                    # (amp[], rate[]) points discarded
    iso_in_h: tuple = ()                   # elapsed hours for the kept points
    iso_out_h: tuple = ()                  # elapsed hours for the discarded points
    verdict: list = field(default_factory=list)


def _polyfit(x, y, deg):
    """
    polyfit that scales x into [-1, 1] first, so a degree-2 fit over, say,
    amplitude 240-265 deg is not wrecked by the x**2 column dwarfing the
    constant one. Returns descending coeffs in the original x, like np.polyfit.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size <= deg:
        return np.zeros(deg + 1)
    try:
        p = np.polynomial.Polynomial.fit(x, y, deg)
        c = np.asarray(p.convert().coef, dtype=float)          # ascending, in x
        if c.size < deg + 1:
            c = np.pad(c, (0, deg + 1 - c.size))
        return c[::-1]
    except Exception:
        return np.polyfit(x, y, deg)


def _robust_polyfit(x, y, deg=1, n_sigma=3.5, iters=3):
    """
    Least-squares polynomial fit that discards gross outliers.

    A power-reserve log run overnight will have the odd sample where the
    pickup caught a knock or a truncated capture -- a point sitting far off
    an otherwise tight trend. Ordinary least squares lets one such point
    swing the slope. This fits, measures the residual spread with the MAD
    (which the outliers themselves barely move), drops anything more than
    n_sigma robust-sigma off the line, and refits, a couple of times.

    Returns (coeffs, keep_mask). keep_mask is all-True if nothing had to go
    or too few points would survive to fit safely.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    keep = np.ones(x.size, dtype=bool)
    if x.size < deg + 2:
        return _polyfit(x, y, deg), keep
    coef = _polyfit(x, y, deg)
    floor = max(deg + 2, int(np.ceil(0.6 * x.size)))
    for _ in range(iters):
        resid = y - np.polyval(coef, x)
        med = np.median(resid[keep])
        mad = np.median(np.abs(resid[keep] - med))
        if mad <= 1e-12:
            break
        good = np.abs(resid - med) <= n_sigma * 1.4826 * mad
        if np.array_equal(good, keep):
            break                        # converged
        if good.sum() < floor:
            break                        # would drop too many -- keep the last fit
        keep = good
        coef = _polyfit(x[keep], y[keep], deg)
    return coef, keep


def reserve_analytics(samples, iso_model: str = "linear") -> ReserveStats:
    a = np.asarray(list(samples), dtype=float)
    st = ReserveStats(n=int(a.shape[0]), iso_model=iso_model)
    if a.ndim != 2 or a.shape[0] < 4:
        return st
    t_h = a[:, 0] / 3600.0
    rate, amp, be = a[:, 1], a[:, 2], a[:, 3]
    st.hours = float(t_h[-1])

    ma = np.isfinite(amp) & np.isfinite(t_h)
    if ma.sum() >= 4:
        st.amp_first, st.amp_last = float(amp[ma][0]), float(amp[ma][-1])
        (sl_a, _), _ = _robust_polyfit(t_h[ma], amp[ma], 1)
        st.amp_per_hour = float(sl_a)
        # The decay accelerates near the end, so extrapolate the runway from
        # the last third rather than the whole-run slope.
        # Post-wind "kick": how fast amplitude falls in the first hour. A steep
        # early drop that then levels off is a mainspring slipping at the barrel
        # wall, or braking grease that has gone hard.
        head = ma & (t_h <= min(t_h[ma][0] + 1.0, t_h[ma][-1]))
        if head.sum() >= 4 and float(t_h[head][-1] - t_h[head][0]) > 0.2:
            (sk, _), _ = _robust_polyfit(t_h[head], amp[head], 1)
            st.kick_deg_per_h = float(-sk)          # positive = amplitude falling

        # Only project a runway once amplitude has genuinely fallen -- more
        # than the measurement scatter and more than ~15 deg. On the torque
        # plateau the tail slope is just noise, and extrapolating it gives the
        # kind of "11 h left on a 72 h watch" nonsense this used to produce.
        _av = amp[ma]
        _fit = np.polyval(np.polyfit(t_h[ma], _av, 1), t_h[ma])
        _scatter = float(np.std(_av - _fit)) if _av.size > 3 else 0.0
        _n3 = max(2, _av.size // 3)
        _decline = float(np.median(_av[:_n3]) - np.median(_av[-_n3:]))
        st.amp_established = bool(_decline >= max(15.0, 3.0 * _scatter))

        cut = t_h[ma][-1] - max(1.0, st.hours / 3.0)
        tail = ma & (t_h >= cut)
        if tail.sum() >= 3 and st.amp_established:
            (sl, ic), _ = _robust_polyfit(t_h[tail], amp[tail], 1)
            if sl < -0.4:
                horizon = t_h[tail][-1] + 3.0 * st.hours + 24.0
                for target, name in ((220.0, "hours_to_220"), (200.0, "hours_to_200")):
                    th = (target - ic) / sl
                    if t_h[tail][0] < th <= horizon:
                        setattr(st, name, float(th))

    mi = np.isfinite(amp) & np.isfinite(rate)
    deg = 2 if iso_model == "quadratic" else 1
    if mi.sum() >= max(6, deg + 4) and float(amp[mi].max() - amp[mi].min()) > 15.0:
        ai, ri = amp[mi], rate[mi]
        coef, keep = _robust_polyfit(ai, ri, deg)
        st.iso_n_out = int((~keep).sum())
        st.iso_in = (ai[keep].tolist(), ri[keep].tolist())
        st.iso_out = (ai[~keep].tolist(), ri[~keep].tolist())
        thi = t_h[mi]
        st.iso_in_h = thi[keep].tolist()
        st.iso_out_h = thi[~keep].tolist()
        st.iso_coef = tuple(float(c) for c in coef)
        span_lo, span_hi = float(ai[keep].min()), float(ai[keep].max())
        mid = 0.5 * (span_lo + span_hi)
        if deg == 2:
            A, B, C = coef
            st.iso_slope = float(2.0 * A * mid + B)        # local sensitivity at mid-range
            st.iso_vertex = float(-B / (2.0 * A)) if abs(A) > 1e-12 else float("nan")
            st.iso_fit = (float(2.0 * A * mid + B), float(np.polyval(coef, mid) -
                                                          (2.0 * A * mid + B) * mid))
            grid = np.linspace(span_lo, span_hi, 64)
            fv = np.polyval(coef, grid)
            st.iso_span = float(fv.max() - fv.min())
        else:
            sl, ic = coef
            st.iso_slope = float(sl)
            st.iso_fit = (float(sl), float(ic))
            st.iso_span = float(sl * (span_hi - span_lo))

    mb = np.isfinite(amp) & np.isfinite(be)
    if mb.sum() >= 5 and float(amp[mb].max() - amp[mb].min()) > 15.0:
        (sl_b, _), _ = _robust_polyfit(amp[mb], be[mb], 1)
        st.be_slope = float(sl_b)

    v = st.verdict
    if st.iso_span == st.iso_span:
        mag = abs(st.iso_span)
        curved = st.iso_model == "quadratic"
        how = ("across the amplitude range covered" if curved
               else f"({st.iso_slope:+.2f} s/day per degree)")
        grade = "good" if mag < 4.0 else "fair" if mag < 12.0 else "poor"
        v.append(f"Isochronism {grade}: {mag:.1f} s/day of rate change {how}." +
                 ("" if grade != "poor" else
                  " The hairspring is not developing evenly -- suspect pinning, a "
                  "sticky terminal curve, or the regulator pins."))
        if curved and st.iso_vertex == st.iso_vertex:
            A = st.iso_coef[0]
            kind = ("flattest" if A > 0 else "steepest")
            v.append(f"Quadratic fit: rate sensitivity is {kind} near {st.iso_vertex:.0f} "
                     f"deg amplitude. Regulating with the balance sitting there gives the "
                     f"least rate drift as the mainspring runs down.")
        if st.iso_n_out:
            v.append(f"{st.iso_n_out} outlier "
                     f"{'reading was' if st.iso_n_out == 1 else 'readings were'} "
                     f"set aside for the fit -- shown dimmed on the plot.")
    if st.be_slope == st.be_slope and abs(st.be_slope) > 0.01:
        v.append(f"Beat error changes {st.be_slope:+.3f} ms per degree of amplitude -- "
                 f"a hairspring that is not breathing concentrically, not a collet that "
                 f"is simply rotated.")
    if st.hours_to_220 == st.hours_to_220:
        v.append(f"On the end-of-run slope, amplitude reaches 220 deg at about "
                 f"{st.hours_to_220:.0f} h" +
                 (f" and 200 deg at {st.hours_to_200:.0f} h." if st.hours_to_200 == st.hours_to_200
                  else "."))
    elif not st.amp_established and st.amp_last == st.amp_last:
        v.append(f"Amplitude is holding near {st.amp_last:.0f} deg -- still on the "
                 f"torque plateau, so there is no runway to extrapolate yet.")
    elif st.amp_per_hour == st.amp_per_hour:
        v.append(f"Amplitude is falling about {abs(st.amp_per_hour):.1f} deg/hour on average.")
    if st.kick_deg_per_h == st.kick_deg_per_h and st.amp_per_hour == st.amp_per_hour:
        overall = abs(st.amp_per_hour)
        if st.kick_deg_per_h > 20 and st.kick_deg_per_h > 3 * max(overall, 0.5):
            v.append(f"Post-wind kick: amplitude drops {st.kick_deg_per_h:.0f} deg in the "
                     f"first hour, far steeper than the {overall:.1f} deg/h average. That "
                     f"is the mainspring slipping at the barrel wall or hard braking "
                     f"grease -- expected on an automatic, a fault on a hand-wind.")
    return st


# --------------------------------------------------------------------------
# Power-reserve forecast -- projects full runtime, sharpening as the run goes
# --------------------------------------------------------------------------

@dataclass
class ReserveForecast:
    ready: bool = False
    full_hours: float = float("nan")       # projected time to `stop_deg`
    low: float = float("nan")              # rough lower / upper bound
    high: float = float("nan")
    stop_deg: float = 135.0
    practical_hours: float = float("nan")  # projected time to `practical_deg` (200)
    practical_deg: float = 200.0
    method: str = ""                       # "quadratic" | "linear (tail)"
    curve_h: "np.ndarray" = field(default_factory=lambda: np.array([]))
    curve_deg: "np.ndarray" = field(default_factory=lambda: np.array([]))
    note: str = ""
    warning: str = ""                      # projection looks unreliable
    rated_hours: float = float("nan")      # caliber's published reserve, if known
    amp_now: float = float("nan")          # smoothed current amplitude
    drop_per_h: float = float("nan")       # robust deg/hour over the run so far
    noise: float = float("nan")            # amplitude scatter about the trend
    smooth_h: "np.ndarray" = field(default_factory=lambda: np.array([]))
    smooth_deg: "np.ndarray" = field(default_factory=lambda: np.array([]))


def _amp_windows(t, amp, minutes=30.0):
    """Median amplitude in `minutes`-wide time windows -- kills the spikes a
    marginal pickup adds before anything is fitted to the decay."""
    if t.size == 0:
        return np.array([]), np.array([])
    w = minutes / 60.0
    edges = np.arange(t[0], t[-1] + w, w)
    bt, ba = [], []
    for i in range(edges.size - 1):
        sel = (t >= edges[i]) & (t < edges[i + 1] + (1e-9 if i == edges.size - 2 else 0))
        if sel.sum() >= 1:
            bt.append(float(np.mean(t[sel])))
            ba.append(float(np.median(amp[sel])))
    return np.asarray(bt), np.asarray(ba)


def reserve_crossings(samples, levels=(220.0, 200.0, 135.0)):
    """
    Elapsed hours at which the amplitude *trend* crossed each level -- the
    smoothed (half-hour median) series must be at or below the level and stay
    there, so a single spike from a marginal pickup is not mistaken for the
    watch winding down. {level: hours}, only for levels genuinely passed.
    """
    a = np.asarray(list(samples), dtype=float)
    out = {}
    if a.ndim != 2 or a.shape[0] < 4:
        return out
    t = a[:, 0] / 3600.0
    amp = a[:, 2]
    ok = np.isfinite(t) & np.isfinite(amp)
    bt, ba = _amp_windows(t[ok], amp[ok], 30.0)
    if bt.size < 3:
        return out
    for lv in levels:
        for i in range(1, bt.size):
            if ba[i - 1] >= lv >= ba[i] and np.median(ba[i:]) <= lv + 3.0:
                frac = ((ba[i - 1] - lv) / (ba[i - 1] - ba[i])
                        if ba[i - 1] != ba[i] else 0.0)
                out[lv] = float(bt[i - 1] + frac * (bt[i] - bt[i - 1]))
                break
    return out


def reserve_headline(samples, rated_hours: float = None) -> dict:
    """
    The two figures a power-reserve run is really about, computed from the raw
    sample series so it stays correct for historical runs even as the method
    improves:
      power_reserve_h -- full wind to the watch running down (~135 deg)
      practical_h     -- full wind to 200 deg, where timekeeping degrades
    Taken from where the run actually crossed each level, else projected from
    the decay ('estimated'); {} if the run is too short to say anything.
    """
    a = np.asarray(list(samples), dtype=float)
    if a.ndim != 2 or a.shape[0] < 3:
        return {}
    amps = a[:, 2][np.isfinite(a[:, 2])]
    if amps.size < 2:
        return {}
    hrs = float(a[-1, 0] / 3600.0)
    cross = reserve_crossings(a)
    fc = reserve_forecast(a, rated_hours=rated_hours)
    last = fc.amp_now if fc.amp_now == fc.amp_now else float(np.median(amps[-5:]))

    prac = cross.get(200.0)
    prac_est = False
    if prac is None:
        if last <= 200.0:
            prac = hrs
        elif fc.ready and fc.practical_hours == fc.practical_hours:
            prac, prac_est = fc.practical_hours, True

    stop = cross.get(135.0)
    stop_est = False
    if stop is None:
        if last <= 135.0:
            stop = hrs
        elif fc.ready:
            stop, stop_est = fc.full_hours, True

    return {"power_reserve_h": stop, "practical_h": prac,
            "estimated": bool(prac_est or stop_est),
            "fc": fc, "last": last, "hrs": hrs,
            "rated": float(rated_hours or 0.0),
            "warning": fc.warning, "holding": (not fc.ready and stop is None)}


def reserve_forecast(samples, stop_deg: float = 135.0, practical_deg: float = 200.0,
                     rated_hours: float = None,
                     min_points: int = 6, min_hours: float = 3.0) -> ReserveForecast:
    """
    From a power-reserve run in progress, project when amplitude reaches
    `stop_deg` (the watch running down) and `practical_deg` (200 deg).

    Mainspring torque holds a near-flat plateau for most of the reserve and
    only steepens near the end, so this refuses to project until amplitude has
    genuinely fallen -- more than measurement noise, and more than ~15 deg. It
    smooths the amplitude into half-hour medians first (a marginal pickup adds
    a lot of spike), fits a robust quadratic, and cross-checks against the
    caliber's rated reserve when one is known.
    """
    fc = ReserveForecast(stop_deg=float(stop_deg), practical_deg=float(practical_deg))
    if rated_hours and rated_hours == rated_hours and rated_hours > 0:
        fc.rated_hours = float(rated_hours)
    a = np.asarray(list(samples), dtype=float)
    if a.ndim != 2 or a.shape[0] < min_points:
        return fc
    t = a[:, 0] / 3600.0
    amp = a[:, 2]
    ok = np.isfinite(t) & np.isfinite(amp)
    t, amp = t[ok], amp[ok]
    span = float(t[-1] - t[0]) if t.size else 0.0
    if t.size < min_points or span < min_hours:
        fc.note = "keep the run going -- a projection needs at least a few hours."
        return fc

    bt, ba = _amp_windows(t, amp, 30.0)
    if bt.size < 4:
        bt, ba = t, amp
    n = bt.size
    third = max(2, n // 3)
    first_med = float(np.median(ba[:third]))
    last_med = float(np.median(ba[-third:]))
    decline = first_med - last_med
    sl, ic = _polyfit(bt, ba, 1)
    noise = float(np.std(ba - (sl * bt + ic)))
    fc.amp_now = last_med
    fc.drop_per_h = float(-sl)
    fc.noise = noise
    fc.smooth_h = np.asarray(bt, dtype=float)
    fc.smooth_deg = np.asarray(ba, dtype=float)

    established = (decline >= max(15.0, 3.0 * noise)) and sl < -0.4
    if not established:
        lost = max(0.0, decline)
        if fc.rated_hours == fc.rated_hours:
            need = 0.6 * fc.rated_hours
            fc.note = (
                f"Amplitude is holding near {last_med:.0f} deg after {t[-1]:.0f} h "
                f"-- {lost:.0f} deg lost, within the {noise:.0f} deg measurement "
                f"scatter. This caliber is rated about {fc.rated_hours:.0f} h; the "
                f"decay does not steepen until roughly {need:.0f} h in, so there is "
                f"nothing firm to project yet.")
        else:
            fc.note = (
                f"Amplitude is still on the torque plateau ({lost:.0f} deg lost in "
                f"{t[-1]:.0f} h, scatter {noise:.0f} deg). A projection needs a "
                f"clear decline -- usually only in the last third of the reserve.")
        return fc

    def _root(coef, target, after):
        c = list(coef)
        c[-1] -= target
        r = np.roots(c)
        r = [float(x.real) for x in r if abs(x.imag) < 1e-6 and x.real > after]
        return min(r) if r else None

    coef2, _ = _robust_polyfit(bt, ba, 2)
    full = _root(coef2, stop_deg, t[-1]) if coef2[0] < 0 else None
    method = "quadratic" if full is not None else ""
    if full is None and sl < -0.4:
        full = (stop_deg - ic) / sl
        method = "linear"
        coef2 = np.array([0.0, sl, ic])
    if full is None or full <= t[-1] or full > t[-1] + 400.0:
        fc.note = "the decline is not consistent enough to project a runtime yet."
        return fc

    lo = hi = full
    k = max(4, int(n * 0.8))
    if k < n:
        ce, _ = _robust_polyfit(bt[:k], ba[:k], 2)
        early = _root(ce, stop_deg, bt[k - 1]) if ce[0] < 0 else None
        if early and early > t[-1]:
            lo, hi = min(full, early), max(full, early)
    pad = max(1.0, 0.10 * full, noise * 0.15)
    fc.ready = True
    fc.full_hours = float(full)
    fc.low = float(max(t[-1], lo - pad))
    fc.high = float(hi + pad)
    fc.method = method
    if last_med > practical_deg:
        prac = _root(coef2, practical_deg, t[-1])
        if prac is not None and prac <= full:
            fc.practical_hours = float(prac)
    grid = np.linspace(float(t[0]), float(full), 60)
    fc.curve_h = grid
    fc.curve_deg = np.polyval(coef2, grid)
    fc.note = (f"projected to run down at ~{full:.1f} h ({fc.low:.1f}-{fc.high:.1f}); "
               f"{method} fit on {n} half-hour points.")
    if fc.rated_hours == fc.rated_hours and full < 0.6 * fc.rated_hours:
        fc.warning = (
            f"This is well under the caliber's rated ~{fc.rated_hours:.0f} h. "
            f"The amplitude trace here is noisy (scatter {noise:.0f} deg), so the "
            f"fit may be reading spikes rather than real decay -- check the "
            f"signal quality before trusting this number.")
    return fc


# --------------------------------------------------------------------------
# Escapement efficiency (impulse fraction)
# --------------------------------------------------------------------------

@dataclass
class EscapementMetrics:
    impulse_fraction: float = float("nan")   # lift angle as a % of the full swing
    free_arc_deg: float = float("nan")       # degrees of unpowered swing per beat
    rating: str = ""                         # excellent | good | fair | poor
    note: str = ""


def escapement_metrics(amplitude: float, lift_angle: float) -> EscapementMetrics:
    """
    The impulse fraction: the balance is pushed through the lift angle and
    swings free for the rest of its arc. lift / (2*amplitude) is the fraction
    of each swing under power. A healthy watch runs mostly free -- ~9-11%.
    A high fraction means low amplitude: the escapement is doing more of the
    work, which is exactly when rate stops holding across positions and wind.
    """
    em = EscapementMetrics()
    if not (np.isfinite(amplitude) and amplitude > 0 and lift_angle > 0):
        return em
    frac = lift_angle / (2.0 * amplitude) * 100.0
    em.impulse_fraction = float(frac)
    em.free_arc_deg = float(2.0 * amplitude - lift_angle)
    if frac < 11.0:
        em.rating = "excellent"
        em.note = ("The balance is running almost entirely free; the escapement is "
                   "only topping up the losses. This is what a freshly serviced "
                   "movement at healthy amplitude looks like.")
    elif frac < 14.0:
        em.rating = "good"
        em.note = "Normal. The escapement's share of the swing is modest."
    elif frac < 18.0:
        em.rating = "fair"
        em.note = ("Amplitude is low enough that the escapement is working a "
                   "noticeable fraction of every swing. Expect the rate to drift "
                   "more with position and as the mainspring runs down.")
    else:
        em.rating = "poor"
        em.note = ("The escapement is carrying much of the arc. Rate and positional "
                   "stability will be unreliable until amplitude is restored -- this "
                   "is a service symptom, not a regulation one.")
    return em


# --------------------------------------------------------------------------
# Long-term rate stability across a watch's whole test history
# --------------------------------------------------------------------------

@dataclass
class HistoryStability:
    n: int = 0
    span_days: float = 0.0
    stdev: float = float("nan")            # run-to-run rate scatter, s/day
    taus: list = field(default_factory=list)     # averaging length in runs
    dev: list = field(default_factory=list)      # overlapping deviation at each tau
    floor: float = float("nan")           # where the curve levels off, s/day
    verdict: str = ""


def history_stability(series) -> HistoryStability:
    """
    `series`: [(timestamp_or_datetime, mean_rate), ...] across a watch's runs.

    Overlapping Allan-style deviation keyed to RUN COUNT rather than seconds,
    because the runs are sparse and irregular. If the deviation keeps falling
    as you average more runs, the scatter is measurement noise and the watch's
    true rate is steady. If it flattens, that floor is how much the rate itself
    wanders between sessions -- no amount of averaging pins it down tighter.
    """
    hs = HistoryStability()
    rows = []
    for ts, rate in series:
        try:
            t = ts.timestamp() if hasattr(ts, "timestamp") else float(ts)
        except (TypeError, ValueError):
            continue
        if rate == rate:
            rows.append((t, float(rate)))
    rows.sort()
    hs.n = len(rows)
    if hs.n < 2:
        hs.verdict = "Two or more runs are needed."
        return hs
    y = np.array([r for _, r in rows])
    hs.span_days = (rows[-1][0] - rows[0][0]) / 86400.0
    hs.stdev = float(np.std(y, ddof=1))
    if hs.n < 5:
        hs.verdict = (f"{hs.n} runs over {hs.span_days:.0f} days; run-to-run scatter "
                      f"{hs.stdev:.1f} s/day. Five or more runs unlock the stability curve.")
        return hs
    csum = np.concatenate([[0.0], np.cumsum(y)])
    for m in (1, 2, 3, 4, 6, 8, 12):
        if m > (hs.n - 1) // 2:
            break
        block = (csum[m:] - csum[:-m]) / m
        d = block[m:] - block[:-m]
        if d.size < 2:
            break
        hs.taus.append(m)
        hs.dev.append(float(np.sqrt(np.mean(d ** 2) / 2.0)))
    if len(hs.dev) >= 2:
        hs.floor = min(hs.dev)
        drop = hs.dev[0] - hs.dev[-1]
        if hs.dev[-1] <= 0.4 * hs.dev[0]:
            hs.verdict = (f"{hs.n} runs over {hs.span_days:.0f} days. The stability "
                          f"curve keeps falling ({hs.dev[0]:.1f} -> {hs.dev[-1]:.1f} "
                          f"s/day as runs are averaged) -- the scatter is measurement "
                          f"noise and the underlying rate is steady.")
        else:
            hs.verdict = (f"{hs.n} runs over {hs.span_days:.0f} days. The stability "
                          f"curve flattens near {hs.floor:.1f} s/day -- the watch's "
                          f"rate genuinely wanders that much between sessions, "
                          f"whatever the regulator is set to.")
    else:
        hs.verdict = (f"{hs.n} runs over {hs.span_days:.0f} days; scatter "
                      f"{hs.stdev:.1f} s/day.")
    return hs


def tuning_score(m: Measurement) -> float:
    """
    How trustworthy does this settings combination look?

    Deliberately does NOT reward a particular amplitude or rate -- tuning
    toward a number you hope to see is how you talk yourself into a wrong
    reading. It rewards only signal-quality evidence:

      quality     how well beats match their own averaged template
      valid_frac  fraction of beats yielding a usable impulse interval
      extra_peaks noises per beat beyond the three a lever escapement makes
      spread      beat-to-beat scatter in the amplitude estimate

    Extra peaks are punished hardest. A pickup resolving 29 noises per beat is
    not measuring an escapement, it is measuring the room.
    """
    if not m.ok or not m.detected_bph:
        return -1e9
    score = 2.5 * m.quality + 1.5 * m.valid_frac
    score -= 0.8 * min(m.extra_peaks, 6.0)
    if m.amplitude != m.amplitude:
        score -= 2.0
    if m.amplitude_spread == m.amplitude_spread:
        score -= 0.02 * min(m.amplitude_spread, 80.0)
    return float(score)


def _try(samples, fs, cfg):
    """
    Score one configuration. A setting that makes the DSP throw is simply a
    bad setting -- some filter bands leave too little signal for the peak
    finder, and on a noisy pickup that is common. Returning a floor score
    keeps the sweep going instead of losing the whole tune to one trial.
    """
    try:
        m = analyze(samples, fs, cfg)
        return tuning_score(m), m
    except Exception:
        return -1e9, None


def autotune(samples: np.ndarray, fs: int, base: AnalyzerConfig,
             progress=None, cancelled=None, deadline=None):
    """
    Sweep filter band, envelope window and sub-noise threshold; return the
    best-scoring configuration plus the trial table.

    Two stages, because the full grid is too slow to sit through: first find
    the band and envelope window that make the beats themselves cleanest,
    then tune the sub-noise threshold against that band.

    `deadline`, if given, is an absolute ``time.monotonic()`` value; the sweep
    returns the best result so far once it is reached. `autotune` cannot be
    interrupted mid-``analyze()``, so the caller's own timeout should allow a
    little slack beyond this.
    """
    def _stop():
        return ((cancelled is not None and cancelled())
                or (deadline is not None and time.monotonic() >= deadline))
    bands = [(600, 6000), (1000, 8000), (1500, 12000), (2500, 12000),
             (3000, 16000), (800, 16000)]
    envs = [0.20, 0.35, 0.60, 1.00]
    thresholds = [0.06, 0.10, 0.14, 0.18, 0.24, 0.30, 0.38, 0.48]

    nyq = fs / 2.0
    bands = [(lo, min(hi, nyq * 0.9)) for lo, hi in bands if lo < nyq * 0.85]

    rows = []
    trials = len(bands) * len(envs) + len(thresholds) + 1
    done = 0

    # Score the current settings first and seed `best` with them, so a sweep
    # that finds nothing better can never regress what the user already has.
    sc0, m0 = _try(samples, fs, base)
    rows.append(("current settings", sc0, m0))
    best, best_score = base, sc0
    done += 1
    if progress:
        progress(done, trials)

    for lo, hi in bands:
        for ew in envs:
            if _stop():
                return best, rows
            cfg = replace(base, band_lo=lo, band_hi=hi, env_win_ms=ew)
            sc, m = _try(samples, fs, cfg)
            rows.append((f"{lo:.0f}-{hi:.0f} Hz, env {ew:.2f} ms", sc, m))
            if sc > best_score:
                best, best_score = cfg, sc
            done += 1
            if progress:
                progress(done, trials)

    for th in thresholds:
        if _stop():
            return best, rows
        cfg = replace(best, sub_threshold=th)
        sc, m = _try(samples, fs, cfg)
        rows.append((f"threshold {th:.2f}", sc, m))
        if sc > best_score:
            best, best_score = cfg, sc
        done += 1
        if progress:
            progress(done, trials)

    return best, rows
