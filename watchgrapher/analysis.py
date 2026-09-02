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
    snapped = snap_bph(m.raw_bph, tol_frac=0.03)
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
                inst = np.convolve(inst, np.ones(k) / k, mode="same")
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
    verdict: list = field(default_factory=list)


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
        return (np.polyfit(x, y, deg) if x.size > deg else np.zeros(deg + 1)), keep
    coef = np.polyfit(x, y, deg)
    floor = max(deg + 2, int(np.ceil(0.6 * x.size)))
    for _ in range(iters):
        resid = y - np.polyval(coef, x)
        med = np.median(resid[keep])
        mad = np.median(np.abs(resid[keep] - med))
        if mad <= 1e-12:
            break
        good = np.abs(resid - med) <= n_sigma * 1.4826 * mad
        if good.sum() < floor or np.array_equal(good, keep):
            if good.sum() >= floor and not np.array_equal(good, keep):
                keep = good
                coef = np.polyfit(x[keep], y[keep], deg)
            break
        keep = good
        coef = np.polyfit(x[keep], y[keep], deg)
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

        cut = t_h[ma][-1] - max(1.0, st.hours / 3.0)
        tail = ma & (t_h >= cut)
        if tail.sum() >= 3:
            (sl, ic), _ = _robust_polyfit(t_h[tail], amp[tail], 1)
            if sl < -1e-3:
                for target, name in ((220.0, "hours_to_220"), (200.0, "hours_to_200")):
                    th = (target - ic) / sl
                    if th > t_h[tail][0]:
                        setattr(st, name, float(th))

    mi = np.isfinite(amp) & np.isfinite(rate)
    deg = 2 if iso_model == "quadratic" else 1
    if mi.sum() >= max(6, deg + 4) and float(amp[mi].max() - amp[mi].min()) > 15.0:
        ai, ri = amp[mi], rate[mi]
        coef, keep = _robust_polyfit(ai, ri, deg)
        st.iso_n_out = int((~keep).sum())
        st.iso_in = (ai[keep].tolist(), ri[keep].tolist())
        st.iso_out = (ai[~keep].tolist(), ri[~keep].tolist())
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
