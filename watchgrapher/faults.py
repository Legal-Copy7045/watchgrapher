"""
Periodic fault detection.

Rate, amplitude and beat error describe the average behaviour of a movement.
They say nothing about whether that average is being produced smoothly. A
bent escape wheel tooth, an eccentric pinion or a damaged balance pivot does
not change the mean rate much at all -- it makes the watch run fast and slow
in a repeating cycle whose period matches the rotation of the guilty part.

On a paper-tape timegrapher this is the wavy or scalloped trace that an
experienced watchmaker reads instantly. The same information is in the fit
residuals, and a periodogram finds it more reliably than an eye can.

The identification step is the useful part: a modulation with a period of one
escape wheel revolution means something quite different from one with a period
of one fourth wheel revolution, and the difference tells you where to look.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


@dataclass
class Periodicity:
    period_beats: float
    period_seconds: float
    amplitude_ms: float       # half-range of the timing swing
    amplitude_spd: float      # what that swing is worth in seconds/day terms
    snr: float                # how far above the noise floor
    component: str            # best guess at the guilty part
    detail: str


@dataclass
class FaultReport:
    ok: bool = False
    message: str = ""
    periods: List[Periodicity] = field(default_factory=list)
    freqs: Optional[np.ndarray] = None      # periods in beats, for plotting
    power: Optional[np.ndarray] = None      # amplitude spectrum, ms
    noise_floor_ms: float = 0.0
    residual_rms_ms: float = 0.0
    horizon_seconds: float = 0.0


# --------------------------------------------------------------------------
# Component identification
# --------------------------------------------------------------------------

def _candidates(bph: float, escape_teeth: int):
    """
    Expected modulation periods, in beats, for the parts that can realistically
    show up inside a bench measurement.

    An escape wheel tooth is released twice per revolution of the wheel per
    tooth -- once by the entry stone and once by the exit -- so one revolution
    spans 2 x teeth beats. Everything upstream of the escape wheel turns more
    slowly and needs a correspondingly longer capture to see.
    """
    bps = bph / 3600.0
    out = [
        (2.0 * escape_teeth, "Escape wheel",
         "One escape wheel revolution. The classic causes are a bent or chipped tooth, "
         "an eccentric or out-of-round wheel, or a bent escape wheel pivot. Look at the "
         "wheel under magnification while it runs -- a single damaged tooth usually "
         "shows as one sharp disturbance per revolution rather than a smooth wave."),
        (escape_teeth * 1.0, "Escape wheel (half cycle)",
         "Half an escape wheel revolution. Often means the fault is symmetric about "
         "the wheel, or that entry and exit stones are unequally worn or unequally "
         "locked."),
        (bps * 60.0, "Fourth wheel / seconds hand",
         "One revolution of the fourth wheel, which carries the seconds hand. A bent "
         "pivot or an eccentric pinion here modulates the whole train once a minute."),
        (bps * 60.0 / 8.0, "Third wheel (approx)",
         "Roughly one third wheel revolution on a typical Swiss train. Gear ratios "
         "vary between calibers, so treat this identification as a hint rather than a "
         "diagnosis."),
    ]
    return out


def _identify(period_beats: float, bph: float, escape_teeth: int):
    best, best_err = None, 1e9
    for expect, name, detail in _candidates(bph, escape_teeth):
        if expect <= 1.5:
            continue
        err = abs(period_beats - expect) / expect
        if err < best_err:
            best, best_err = (name, detail), err
    if best is None or best_err > 0.12:
        secs = period_beats * 3600.0 / bph
        return ("Unidentified", (
            f"A repeating {secs:.1f} second cycle that does not line up with the escape "
            f"wheel or the usual train wheels. Work out which wheel turns once every "
            f"{secs:.1f} seconds in this caliber and inspect that one. Also worth ruling "
            f"out something outside the movement -- a rotor swinging, or the watch "
            f"rocking on the pickup."))
    return best


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

def analyze_periodicity(index: np.ndarray, resid: np.ndarray, bph: float,
                        escape_teeth: int = 15, min_snr: float = 4.0,
                        max_report: int = 3, min_ms: float = 0.02) -> FaultReport:
    """
    Periodogram of the timing residuals against beat number.

    The beat-error alternation is removed first. It is a real and expected
    two-beat modulation, it dominates the spectrum when present, and it is
    already reported as its own number -- leaving it in would mask everything
    else.
    """
    rep = FaultReport()
    idx = np.asarray(index, dtype=float)
    res = np.asarray(resid, dtype=float)
    if idx.size < 64:
        rep.message = ("Not enough beats for periodic analysis. Widen the analysis "
                       "window to 30 seconds or more.")
        return rep

    # Strip the tick/tock alternation.
    res = res.copy()
    for p in (0, 1):
        sel = idx % 2 == p
        if sel.sum():
            res[sel] -= np.mean(res[sel])

    # Detections can skip a beat; resample onto a uniform beat grid.
    lo, hi = int(idx.min()), int(idx.max())
    grid = np.arange(lo, hi + 1, dtype=float)
    if grid.size < 64:
        rep.message = "Not enough beats for periodic analysis."
        return rep
    order = np.argsort(idx)
    y = np.interp(grid, idx[order], res[order])
    y = y - y.mean()
    rep.residual_rms_ms = float(np.sqrt(np.mean(y ** 2)) * 1000.0)

    n = y.size
    win = np.hanning(n)
    spec = np.fft.rfft(y * win)
    # Amplitude in ms, corrected for the window's coherent gain.
    amp = np.abs(spec) * 2.0 / (np.sum(win)) * 1000.0
    k = np.arange(amp.size)
    with np.errstate(divide="ignore"):
        periods = np.where(k > 0, n / np.maximum(k, 1e-9), np.inf)

    # Only periods we can actually resolve: at least 3 cycles in the capture.
    # The floor sits above 2 beats deliberately -- a two-beat cycle IS beat
    # error, it is reported as its own number, and whatever leaks past the
    # parity removal above is not a separate fault. A genuine impulse-jewel or
    # roller fault also lands at 2 beats and is not separable from beat error
    # by this method.
    usable = (periods >= 2.6) & (periods <= n / 3.0)
    if usable.sum() < 8:
        rep.message = "Capture too short to resolve any periodic modulation."
        return rep

    rep.freqs = periods[usable]
    rep.power = amp[usable]
    floor = float(np.median(amp[usable])) + 1e-9
    rep.noise_floor_ms = floor

    from scipy import signal as _sig
    pk, _ = _sig.find_peaks(amp[usable], height=max(floor * min_snr, min_ms))
    if pk.size:
        heights = amp[usable][pk]
        order2 = np.argsort(heights)[::-1][:max_report]
        for j in order2:
            i = pk[j]
            pb = float(periods[usable][i])
            a = float(amp[usable][i])
            name, detail = _identify(pb, bph, escape_teeth)
            secs = pb * 3600.0 / bph
            # A timing swing of +/-a ms repeating every `secs` seconds is worth
            # this much instantaneous rate excursion.
            spd = a / 1000.0 / max(secs, 1e-9) * 86400.0
            rep.periods.append(Periodicity(pb, secs, a, spd, a / floor, name, detail))

    rep.ok = True
    # Be explicit about the horizon. A periodogram needs about three cycles to
    # call a period real, so a 60 second capture at 4 Hz cannot see a fourth
    # wheel at all -- and saying nothing would read as a clean bill of health.
    longest_s = (n / 3.0) * 3600.0 / bph
    rep.horizon_seconds = float(longest_s)
    if not rep.periods:
        rep.message = (
            f"No significant periodic modulation down to {floor:.3f} ms.\n\n"
            f"This capture can only resolve cycles up to about {longest_s:.0f} seconds "
            f"long -- three repeats are needed before a period can be called real. "
            f"A fourth wheel turns once a minute and a centre wheel once an hour, so "
            f"to rule those out you would need a capture of at least "
            f"{3 * 60:.0f} and {3 * 3600:.0f} seconds respectively.")
    return rep


def summarise(rep: FaultReport) -> str:
    if not rep.ok or not rep.periods:
        return rep.message
    out = [f"Residual RMS {rep.residual_rms_ms:.3f} ms, noise floor "
           f"{rep.noise_floor_ms:.3f} ms, resolvable up to "
           f"{rep.horizon_seconds:.0f} s.\n"]
    for p in rep.periods:
        sev = "warn" if p.amplitude_ms > 0.15 else "info"
        out.append(
            f"[{sev.upper()}] {p.component}: {p.amplitude_ms:.3f} ms swing every "
            f"{p.period_seconds:.2f} s ({p.period_beats:.1f} beats, {p.snr:.1f}x noise)\n"
            f"    {p.detail}")
    return "\n\n".join(out)
