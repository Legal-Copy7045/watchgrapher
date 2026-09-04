"""
Signal processing for acoustic watch timing.

The chain, in order:

  1. Band-pass the raw microphone signal. Escapement noise lives roughly
     1.5-12 kHz; room rumble, handling and mains hum live below that.
  2. Build a short-time energy envelope.
  3. Estimate the beat period by autocorrelating the envelope. This is
     immune to which of the three sub-noises happens to be loudest.
  4. Coarse-detect one event per beat.
  5. Refine every beat time by cross-correlating against an averaged
     template, with parabolic interpolation for sub-sample resolution.
     Tick and tock get SEPARATE templates -- the entry and exit pallet
     stones do not sound alike, and using one shared template injects a
     fixed bias straight into the beat-error reading.
  6. Inside each beat, locate the 1st noise (unlocking) and the 3rd noise
     (drop/lock onto the opposite pallet stone). The interval between them
     is what amplitude is computed from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import signal


# --------------------------------------------------------------------------
# Filtering and envelope
# --------------------------------------------------------------------------

def design_bandpass(fs: int, lo: float = 1500.0, hi: float = 12000.0, order: int = 4):
    nyq = fs / 2.0
    hi = min(hi, nyq * 0.95)
    lo = max(lo, 20.0)
    if lo >= hi:
        lo = hi * 0.1
    return signal.butter(order, [lo / nyq, hi / nyq], btype="band", output="sos")


def bandpass(x: np.ndarray, fs: int, lo: float = 1500.0, hi: float = 12000.0) -> np.ndarray:
    sos = design_bandpass(fs, lo, hi)
    return signal.sosfiltfilt(sos, x).astype(np.float64)


def envelope(x: np.ndarray, fs: int, win_ms: float = 0.35) -> np.ndarray:
    """
    Short-time energy envelope. The window is deliberately short (a few
    hundred microseconds) so the three sub-noises inside one beat stay
    resolvable -- they are only a handful of milliseconds apart.
    """
    n = max(3, int(round(fs * win_ms / 1000.0)))
    if n % 2 == 0:
        n += 1
    kernel = np.hanning(n)
    kernel /= kernel.sum()
    # Return an RMS (amplitude) envelope, not raw energy. Relative peak
    # heights then scale linearly with sound pressure, so a threshold of
    # "16% of the loudest sub-noise" means what it sounds like. On a squared
    # envelope the same threshold would be 16% of *energy*, which silently
    # rejects the quiet unlocking noise and breaks amplitude.
    return np.sqrt(np.convolve(x * x, kernel, mode="same"))


# --------------------------------------------------------------------------
# Beat period estimation
# --------------------------------------------------------------------------

def estimate_beat_period(env: np.ndarray, fs: int,
                         bph_min: int = 12000, bph_max: int = 43200) -> Optional[float]:
    """
    Autocorrelate the envelope and return the beat period in seconds.

    Returns the SMALLEST lag that is close to the global maximum, because a
    signal with a genuine period T also correlates strongly at 2T, 3T...
    and we want the fundamental, not a harmonic.
    """
    if env.size < fs // 4:
        return None

    e = env - env.mean()
    if not np.any(e):
        return None

    # Autocorrelation via FFT.
    n = int(2 ** np.ceil(np.log2(len(e) * 2)))
    spec = np.fft.rfft(e, n)
    ac = np.fft.irfft(spec * np.conj(spec), n)[: len(e)]
    if ac[0] <= 0:
        return None
    ac /= ac[0]

    lag_min = int(fs * 3600.0 / bph_max)   # shortest plausible beat
    lag_max = int(fs * 3600.0 / bph_min)   # longest plausible beat
    lag_max = min(lag_max, len(ac) - 2)
    if lag_max <= lag_min + 2:
        return None

    seg = ac[lag_min:lag_max]
    peaks, props = signal.find_peaks(seg, height=0.05, distance=max(2, lag_min // 4))
    if peaks.size == 0:
        return None

    heights = props["peak_heights"]
    strong = peaks[heights >= 0.55 * heights.max()]
    lag = int(strong.min() if strong.size else peaks[np.argmax(heights)]) + lag_min

    # Sub-harmonic guard. Tick and tock never sound quite alike -- entry and
    # exit pallet stones differ, and any beat error offsets them in time -- so
    # the envelope often correlates more strongly at TWO beats than at one.
    # Left alone that reports half the true bph. If there is real correlation
    # at half the chosen lag, that half is the fundamental.
    for _ in range(2):
        half = lag // 2
        if half < lag_min or half >= len(ac) - 1:
            break
        near = ac[max(0, half - 2): half + 3]
        if near.size and near.max() > 0.32 * ac[min(lag, len(ac) - 1)] and near.max() > 0.08:
            lag = half + int(np.argmax(near)) - 2
        else:
            break

    # Parabolic refinement of the autocorrelation peak.
    if 0 < lag < len(ac) - 1:
        y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = y0 - 2 * y1 + y2
        if denom != 0:
            lag = lag + 0.5 * (y0 - y2) / denom

    return float(lag) / fs


# --------------------------------------------------------------------------
# Beat detection and sub-sample refinement
# --------------------------------------------------------------------------

@dataclass
class BeatSet:
    times: np.ndarray                 # refined beat times, seconds from buffer start
    raw_index: np.ndarray             # coarse sample index of each detected beat
    period_est: float                 # seconds per beat, from autocorrelation
    snr_db: float = 0.0
    template_tick: Optional[np.ndarray] = None
    template_tock: Optional[np.ndarray] = None
    template_pre: int = 0             # samples of template before the anchor
    quality: float = 0.0              # mean normalised correlation, 0..1


def verify_period(env: np.ndarray, fs: int, period: float) -> float:
    """
    Cross-check the autocorrelation estimate against actual peak spacing.

    Autocorrelation can settle on twice the beat interval when tick and tock
    differ enough -- a large beat error alone will do it, since it pushes the
    two half-periods apart in time. Detecting peaks with a refractory shorter
    than one beat and then reading the median spacing settles the question
    directly: if the peaks really are twice as dense as the estimate claims,
    the estimate was a harmonic.

    The halving is only applied when the peaks form a REGULAR grid (coefficient
    of variation of the spacing a few percent) and stand well clear of the
    floor. On a weak or noisy pickup the peak detector fires on the noise floor
    roughly every refractory interval -- about half a beat -- with wildly
    uneven spacing, and the old code read that as "the real rate is double",
    which is exactly the "80,000 bph" nonsense a marginal signal used to
    produce.
    """
    dist = max(3, int(0.38 * period * fs))
    if env.max() <= 0:
        return period
    e = env / env.max()
    floor = np.median(e)
    prom = max(0.10, 4.0 * (np.median(np.abs(e - floor)) + 1e-12))
    idx, _ = signal.find_peaks(e, distance=dist, prominence=min(prom, 0.5))
    if idx.size < 8:
        return period

    d = np.diff(idx)
    med = float(np.median(d)) / fs
    # Coefficient of variation of the spacing. Genuine sub-beats land on a
    # near-perfect grid (CV a few percent); noise peaks fired at roughly the
    # refractory interval scatter badly (CV > 0.15). This is what separates a
    # real tick/tock pair from the noise floor that used to read as "double".
    cv = float(np.std(d) / (np.median(d) + 1e-9))
    med_height = float(np.median(e[idx]))

    if 0.40 * period < med < 0.64 * period:
        if cv < 0.10 and med_height > 0.5:
            return period / 2.0
        return period
    if 1.55 * period < med < 2.60 * period:
        if cv < 0.15:
            return period * 2.0
    return period


def detect_beats(env: np.ndarray, fs: int, period: float,
                 prominence_frac: float = 0.10) -> np.ndarray:
    """Coarse: one detection per beat, using a refractory distance."""
    dist = max(4, int(0.55 * period * fs))
    if env.max() <= 0:
        return np.array([], dtype=int)
    e = env / env.max()
    # A robust floor: most of the buffer is silence between beats.
    floor = np.median(e)
    prom = max(prominence_frac, 4.0 * (np.median(np.abs(e - floor)) + 1e-12))
    idx, _ = signal.find_peaks(e, distance=dist, prominence=min(prom, 0.5))
    return idx


def _extract(x: np.ndarray, centers: np.ndarray, pre: int, post: int) -> np.ndarray:
    """Stack windows around each center; drop any that fall off the edges."""
    ok = (centers >= pre) & (centers < len(x) - post)
    c = centers[ok]
    if c.size == 0:
        return np.zeros((0, pre + post))
    offs = np.arange(-pre, post)
    return x[c[:, None] + offs[None, :]]


def _parabolic_peak(y: np.ndarray, i: int) -> float:
    if i <= 0 or i >= len(y) - 1:
        return float(i)
    d = y[i - 1] - 2 * y[i] + y[i + 1]
    if d == 0:
        return float(i)
    return i + 0.5 * (y[i - 1] - y[i + 1]) / d


def refine_beats(xf: np.ndarray, fs: int, coarse: np.ndarray, period: float,
                 pre_ms: float = 12.0, post_ms: float = 15.0,
                 iterations: int = 2) -> BeatSet:
    """
    Cross-correlate each beat against an averaged template to get sub-sample
    timing. Even-indexed and odd-indexed beats are templated separately so
    the tick/tock waveform difference cannot leak into beat error.
    """
    # Never let the template window reach into the neighbouring beat.
    pre = int(min(fs * pre_ms / 1000.0, 0.28 * period * fs))
    post = int(min(fs * post_ms / 1000.0, 0.32 * period * fs))
    if coarse.size < 8:
        return BeatSet(coarse.astype(float) / fs, coarse, period)

    centers = coarse.astype(np.float64)
    tpl = [None, None]
    quality = 0.0

    for _ in range(iterations):
        ic = np.rint(centers).astype(int)
        ok = (ic >= pre) & (ic < len(xf) - post)
        if ok.sum() < 8:
            break
        parity = np.arange(len(ic)) % 2
        shifts = np.zeros(len(ic))
        corrs = np.zeros(len(ic))

        for p in (0, 1):
            sel = np.where(ok & (parity == p))[0]
            if sel.size < 3:
                continue
            block = _extract(xf, ic[sel], pre, post)
            if block.shape[0] == 0:
                continue
            t = block.mean(axis=0)
            t = t - t.mean()
            tn = np.linalg.norm(t)
            if tn == 0:
                continue
            t = t / tn
            tpl[p] = t
            for k, row in zip(sel, block):
                r = row - row.mean()
                rn = np.linalg.norm(r)
                if rn == 0:
                    continue
                # Correlate over a modest search range (+/- 1.5 ms).
                lim = int(fs * 1.5 / 1000.0)
                cc = signal.correlate(r / rn, t, mode="same")
                mid = len(cc) // 2
                lo, hi = max(0, mid - lim), min(len(cc), mid + lim + 1)
                j = lo + int(np.argmax(cc[lo:hi]))
                shifts[k] = _parabolic_peak(cc, j) - mid
                corrs[k] = float(cc[j])

        # The two parity templates are each built around their own detection
        # anchor, and those anchors need not sit on the same physical noise:
        # if the drop is loudest in a tick but the impulse is loudest in a
        # tock, the coarse detector locks onto noise 3 for one and noise 2 for
        # the other. Correlating each beat against its own template then bakes
        # that offset into every odd beat -- which is indistinguishable from
        # beat error, and can invent several milliseconds of it on a watch
        # that is perfectly in beat. Measure the offset between the two
        # templates and take it back out. Real beat error lives in WHEN the
        # beats occur, which this does not touch.
        if tpl[0] is not None and tpl[1] is not None and len(tpl[0]) == len(tpl[1]):
            cc = signal.correlate(tpl[1], tpl[0], mode="same")
            mid = len(cc) // 2
            lim = int(fs * 4.0 / 1000.0)
            a_, b_ = max(0, mid - lim), min(len(cc), mid + lim + 1)
            j = a_ + int(np.argmax(cc[a_:b_]))
            d = _parabolic_peak(cc, j) - mid
            shifts[parity == 1] += d

        centers = centers + shifts
        quality = float(np.mean(np.clip(corrs[ok], 0, 1))) if ok.any() else 0.0

    # Rough SNR: peak energy at the beats vs. the quiet floor between them.
    env_pk = np.abs(xf[np.clip(np.rint(centers).astype(int), 0, len(xf) - 1)])
    floor = np.median(np.abs(xf)) + 1e-12
    snr = 20.0 * np.log10((np.median(env_pk) + 1e-12) / floor)

    return BeatSet(centers / fs, coarse, period, snr, tpl[0], tpl[1], pre, quality)


# --------------------------------------------------------------------------
# Impulse interval (drives amplitude)
# --------------------------------------------------------------------------

@dataclass
class ImpulseResult:
    dt: np.ndarray                    # seconds between 1st and 3rd noise, per beat
    valid: np.ndarray                 # boolean mask
    mean_shape: Optional[np.ndarray] = None   # averaged envelope of one beat
    shape_fs: int = 0
    shape_pre: int = 0
    p1_idx: float = 0.0               # index into mean_shape of the unlocking noise
    p3_idx: float = 0.0               # index into mean_shape of the drop/lock noise
    extra_peaks: float = 0.0          # mean count of noises beyond the expected three
    p1_off: Optional[np.ndarray] = None   # per-beat: unlocking position minus anchor, samples


def dt_bounds(bph: float, lift_angle: float,
              amp_min: float = 90.0, amp_max: float = 360.0) -> tuple:
    """
    Physically plausible window for the 1st-to-3rd noise interval.

    Balance motion is close to simple harmonic:  theta(t) = A sin(2*pi*t/T_osc),
    with the lift arc centred on the neutral point, so

        lift/2 = A * sin(pi * dt / T_osc),   T_osc = 2 * 3600 / bph.

    Inverting for dt gives the bounds below.
    """
    t_osc = 2.0 * 3600.0 / bph
    def dt_for(a):
        s = np.clip((lift_angle / 2.0) / max(a, 1e-6), -1.0, 1.0)
        return (t_osc / np.pi) * np.arcsin(s)
    return dt_for(amp_max), dt_for(amp_min)


def _group_span(peaks: np.ndarray, anchor: int, max_gap: int, heights=None):
    """
    The three sub-noises of one beat form a tight cluster. Starting from the
    peak nearest the anchor, walk outward while consecutive gaps stay inside
    max_gap, and return (first, last) of that cluster.

    This matters because the coarse detector locks onto whichever sub-noise
    is loudest -- usually the drop, which is the LAST of the three. Searching
    only forward from the anchor would miss the unlocking noise entirely and
    report a wildly high amplitude.
    """
    if peaks.size == 0:
        return None, 0
    i = int(np.argmin(np.abs(peaks - anchor)))
    lo = i
    while lo > 0 and (peaks[lo] - peaks[lo - 1]) <= max_gap:
        lo -= 1
    hi = i
    while hi < peaks.size - 1 and (peaks[hi + 1] - peaks[hi]) <= max_gap:
        hi += 1

    # A lever escapement makes exactly THREE noises per beat, in a fixed
    # order: unlocking, impulse, then drop onto the opposite stone. Anything
    # arriving after the drop -- case resonance, a rotor, a reflection off the
    # bench -- is not part of that sequence. Taking the last peak in the
    # cluster lets such an echo stretch the measured interval and drag the
    # amplitude reading far below the truth, so cap the group at the first
    # three peaks.
    n_found = hi - lo + 1

    # End the span on the DROP, which is the loudest of the three noises on
    # essentially every lever escapement -- it is the escape wheel tooth
    # slamming onto the opposite pallet stone. An echo is by definition
    # quieter than whatever produced it, so ending on the loudest peak
    # rejects it even when one of the genuine three fell below threshold and
    # a simple count of peaks would not.
    end = min(hi, lo + 2)
    if heights is not None and hi > lo:
        seg = heights[lo:hi + 1]
        j = lo + int(np.argmax(seg))
        if j > lo:
            end = j
    return (int(peaks[lo]), int(peaks[end])), n_found


def measure_impulse(env: np.ndarray, fs: int, beats: BeatSet, bph: float,
                    lift_angle: float, rel_threshold: float = 0.16,
                    amp_min: float = 120.0, amp_max: float = 355.0) -> ImpulseResult:
    """
    For every beat, isolate the cluster of sub-noises and measure the span
    from the first (unlocking) to the last (drop/lock).

    `rel_threshold` is the fraction of that beat's own peak envelope a
    sub-noise must reach to count. The unlocking noise is the quietest of
    the three, so this is the knob to turn: too high and p1 is missed, dt
    comes out short and amplitude reads high; too low and room noise gets
    counted as p1, making amplitude read low and jump around.
    """
    lo_dt, hi_dt = dt_bounds(bph, lift_angle, amp_min, amp_max)
    period = 3600.0 / bph

    # Look both ways from the anchor, but never far enough to touch a neighbour.
    half = int(min(fs * (hi_dt + 0.004), 0.42 * period * fs))
    pre = post = half

    ic = np.rint(beats.times * fs).astype(int)
    ok = (ic >= pre) & (ic < len(env) - post)
    dt = np.full(len(ic), np.nan)
    p1_off = np.full(len(ic), np.nan)

    lo_n, hi_n = int(lo_dt * fs), int(hi_dt * fs)
    min_sep = max(2, int(fs * 0.30 / 1000.0))
    max_gap = hi_n

    block_rows = []
    extra = 0
    for k in np.where(ok)[0]:
        seg = env[ic[k] - pre: ic[k] + post]
        if seg.size < 8 or seg.max() <= 0:
            continue
        s = seg / seg.max()
        block_rows.append(s)
        pk, pr = signal.find_peaks(s, height=rel_threshold, distance=min_sep)
        span, nf = _group_span(pk, pre, max_gap, pr.get("peak_heights"))
        if span is None:
            continue
        extra += max(0, nf - 3)
        p1_off[k] = span[0] - pre
        width = span[1] - span[0]
        if lo_n <= width <= hi_n:
            dt[k] = width / fs

    valid = np.isfinite(dt)

    shape = None
    p1 = p3 = 0.0
    if block_rows:
        L = min(len(r) for r in block_rows)
        shape = np.mean([r[:L] for r in block_rows], axis=0)
        shape = shape / (shape.max() + 1e-12)
        pk, pr = signal.find_peaks(shape, height=rel_threshold, distance=min_sep)
        span, _nf = _group_span(pk, pre, max_gap, pr.get("peak_heights"))
        if span is not None:
            p1, p3 = float(span[0]), float(span[1])

    n_ok = max(1, int(np.count_nonzero(ok)))
    return ImpulseResult(dt, valid, shape, fs, pre, p1, p3, extra / n_ok, p1_off)
