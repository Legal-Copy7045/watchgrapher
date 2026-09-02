"""
Fault signature library.

The periodic scan says which part is modulating the rate. The four numbers
say how the watch is behaving. On their own each is a clue; together they
often point at one specific fault. This module encodes the patterns an
experienced watchmaker carries in their head -- "high rate, normal
amplitude, and it moved when demagnetised" is a magnetised hairspring --
as a set of signatures, each scoring how well the current picture fits it.

Nothing here touches the movement. Every match ends with what to look at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

# Position names as used by advisor.POSITIONS. Kept local to avoid importing
# advisor (which pulls in the caliber tables) just for two sets.
_HORIZONTAL = ("Dial up", "Dial down")
_VERTICAL = ("Crown down", "Crown left", "Crown right", "Crown up")


@dataclass
class SymptomContext:
    # Single-reading picture (dial up, full wind, best available).
    rate: float = float("nan")
    amplitude: float = float("nan")
    beat_error: float = float("nan")
    amplitude_spread: float = float("nan")     # IQR of per-beat amplitude, deg
    extra_peaks: float = 0.0                   # noises per beat beyond 3
    quality: float = float("nan")             # template match 0..1
    snr_db: float = float("nan")

    # Caliber expectations.
    amp_full_wind: tuple = (250.0, 315.0)
    bph: Optional[int] = None

    # Positional set, if captured: {position_name: (rate, amplitude, beat_error)}.
    positions: dict = field(default_factory=dict)

    # Periodic scan peaks: list of (component, amplitude_ms, snr).
    periodic: list = field(default_factory=list)

    # Demagnetiser A/B, if run: (delta_rate_spd, delta_amplitude_deg).
    demag_delta: Optional[tuple] = None

    # Power-reserve isochronism, if a run exists: peak-to-trough s/day.
    iso_span: float = float("nan")

    # Post-wind amplitude kick, if measured: deg lost in the first hour.
    kick_deg_per_h: float = float("nan")

    def horiz_rates(self):
        return [v[0] for k, v in self.positions.items()
                if k in _HORIZONTAL and v[0] == v[0]]

    def vert_rates(self):
        return [v[0] for k, v in self.positions.items()
                if k in _VERTICAL and v[0] == v[0]]

    def positional_delta(self):
        rs = [v[0] for v in self.positions.values() if v[0] == v[0]]
        return (max(rs) - min(rs)) if len(rs) >= 2 else float("nan")

    def vertical_amp_drop(self):
        du = self.positions.get("Dial up", (float("nan"),) * 3)[1]
        verts = [v[1] for k, v in self.positions.items()
                 if k not in ("Dial up", "Dial down") and v[1] == v[1]]
        if du == du and verts:
            return du - float(np.mean(verts))
        return float("nan")


@dataclass
class Signature:
    name: str
    confidence: float
    why: str
    check: str


def _amp_low(ctx) -> float:
    """0..1: how far below the caliber's full-wind band the amplitude sits."""
    lo = ctx.amp_full_wind[0] if ctx.amp_full_wind else 250.0
    if ctx.amplitude != ctx.amplitude:
        return 0.0
    return float(np.clip((lo - ctx.amplitude) / 40.0, 0.0, 1.0))


def _peak(ctx, needle):
    for comp, ms, snr in ctx.periodic:
        if needle.lower() in str(comp).lower():
            return float(ms), float(snr)
    return 0.0, 0.0


def match(ctx: SymptomContext) -> List[Signature]:
    out: List[Signature] = []

    # -- magnetism -----------------------------------------------------------
    conf = 0.0
    why = []
    if ctx.demag_delta is not None:
        dr, da = ctx.demag_delta
        if dr < -8 and abs(da) < 25:
            conf = 0.9
            why.append(f"demagnetising moved the rate {dr:+.1f} s/day with amplitude "
                       f"barely changed ({da:+.0f} deg)")
    if conf == 0.0 and ctx.rate > 15 and _amp_low(ctx) < 0.3 \
            and ctx.beat_error == ctx.beat_error and ctx.beat_error < 1.5:
        conf = 0.4
        why.append(f"running {ctx.rate:+.0f} s/day fast with normal amplitude and beat "
                   f"error -- the classic magnetised-hairspring pattern")
    if conf:
        out.append(Signature(
            "Magnetised hairspring", conf, "; ".join(why),
            "Run the watch through a demagnetiser and re-measure. If the rate drops "
            "back, every position measured while magnetised must be redone."))

    # -- escape wheel fault -----------------------------------------------------
    ms, snr = _peak(ctx, "escape wheel")
    if ms > 0.08 and snr > 4:
        out.append(Signature(
            "Escape wheel tooth or eccentricity",
            float(np.clip(0.3 + ms, 0.0, 0.95)),
            f"a {ms:.2f} ms swing repeats once per escape-wheel turn ({snr:.0f}x noise)",
            "Watch the escape wheel under magnification while it runs. One bent or "
            "chipped tooth shows as a single sharp jump per revolution; an eccentric "
            "or bent-pivot wheel shows a smooth wave. Also check escape-wheel endshake."))

    # -- fourth / train wheel fault ------------------------------------------
    ms, snr = _peak(ctx, "fourth wheel")
    if ms > 0.08 and snr > 4:
        out.append(Signature(
            "Fourth-wheel pivot or pinion",
            float(np.clip(0.3 + ms, 0.0, 0.9)),
            f"a {ms:.2f} ms swing repeats once a minute ({snr:.0f}x noise)",
            "Check the fourth-wheel pivots and pinion for a bend or eccentricity, and "
            "its endshake. A rubbing seconds hand or a fouled pinion leaf does this too."))

    # -- poise error -------------------------------------------------------------
    if ctx.positions:
        du_dd = ctx.horiz_rates()
        verts = ctx.vert_rates()
        pd = ctx.positional_delta()
        horiz_spread = (max(du_dd) - min(du_dd)) if len(du_dd) >= 2 else float("nan")
        vert_spread = (max(verts) - min(verts)) if len(verts) >= 2 else float("nan")
        # Poise error: the vertical positions disagree while dial-up and
        # dial-down match -- a heavy spot on the balance rim, not the hairspring.
        if (pd == pd and pd > 18 and (horiz_spread != horiz_spread or horiz_spread < 8)
                and (vert_spread != vert_spread or vert_spread > 10)):
            out.append(Signature(
                "Balance poise error",
                float(np.clip(pd / 40.0, 0.2, 0.9)),
                f"{pd:.0f} s/day spread across positions while dial-up and dial-down "
                f"agree -- weight distribution, not the hairspring",
                "Dynamic poise the balance: find the position with the slowest vertical "
                "rate, that is where the heavy spot sits. On a screw balance adjust the "
                "opposing screws; on a smooth balance this is a poising-tool job."))

    # -- hairspring out of flat / rubbing ----------------------------------------
    if ctx.positions:
        va = ctx.vertical_amp_drop()
        pd = ctx.positional_delta()
        if pd == pd and pd > 15 and va == va and va > 45:
            out.append(Signature(
                "Hairspring out of flat or rubbing",
                float(np.clip(0.25 + pd / 60.0, 0.2, 0.85)),
                f"{pd:.0f} s/day positional spread with a {va:.0f} deg vertical amplitude "
                f"drop -- the coil is touching or the spring is not lying flat",
                "Look at the hairspring from the side with the balance running: it "
                "should stay in one plane and the coils evenly spaced. Check for a "
                "coil touching the stud, the regulator pins, or an adjacent coil."))

    # -- low amplitude: power delivery ----------------------------------------
    la = _amp_low(ctx)
    if la > 0.35 and ctx.extra_peaks < 1.0 and (ctx.quality != ctx.quality or ctx.quality > 0.7):
        conf = float(np.clip(0.3 + la * 0.5, 0.0, 0.85))
        why = f"amplitude {ctx.amplitude:.0f} deg is well under this caliber's "\
              f"{ctx.amp_full_wind[0]:.0f}+ at full wind, but the beat is clean"
        if ctx.kick_deg_per_h == ctx.kick_deg_per_h and ctx.kick_deg_per_h > 25:
            conf = min(0.9, conf + 0.15)
            why += f"; amplitude also falls fast right after winding ({ctx.kick_deg_per_h:.0f} deg/h)"
        out.append(Signature(
            "Low amplitude -- power delivery", conf, why,
            "Dried or migrated oil in the train or barrel, a tired or set mainspring, "
            "or braking grease gone hard. Service the barrel and train; if it was "
            "recently serviced, check mainspring choice and barrel-wall lubrication."))

    # -- low amplitude: escapement -----------------------------------------------
    if la > 0.2 and (ctx.extra_peaks > 1.2 or (ctx.quality == ctx.quality and ctx.quality < 0.6)):
        out.append(Signature(
            "Low amplitude -- escapement",
            float(np.clip(0.3 + la * 0.4 + min(ctx.extra_peaks, 3) * 0.1, 0.0, 0.9)),
            f"low amplitude with a noisy or poorly-repeating beat "
            f"({3 + ctx.extra_peaks:.1f} noises/beat, match {ctx.quality:.2f})",
            "Look at the pallet stones and escape teeth for wear, chips or dried oil, "
            "and check pallet-fork and balance endshake and the guard pin / safety "
            "roller clearance."))

    # -- rebanking / knocking --------------------------------------------------
    if ctx.amplitude == ctx.amplitude and ctx.amplitude > 330:
        out.append(Signature(
            "Rebanking (knocking)",
            float(np.clip((ctx.amplitude - 320) / 40.0, 0.3, 0.95)),
            f"amplitude {ctx.amplitude:.0f} deg -- at or past 360 the impulse pin hits "
            f"the fork outside the horns and the balance slams the banking",
            "Usually a mainspring that is too strong for the movement after a service, "
            "or over-oiled/too-slippery escapement. Fit the correct mainspring; check "
            "escapement lubrication. Sustained knocking damages the impulse pin."))

    # -- isochronism / hairspring pinning ------------------------------------
    if ctx.iso_span == ctx.iso_span and abs(ctx.iso_span) > 12:
        out.append(Signature(
            "Poor isochronism -- hairspring",
            float(np.clip(abs(ctx.iso_span) / 40.0, 0.3, 0.9)),
            f"rate moves {ctx.iso_span:+.0f} s/day as amplitude falls over a "
            f"power-reserve run",
            "Check the hairspring pinning at the collet and stud, the terminal curve "
            "shape, and that the spring is centred in the regulator pins with even "
            "play. A spring that is not developing concentrically does this."))

    # -- just a beat-error reset -----------------------------------------------
    if ctx.beat_error == ctx.beat_error and ctx.beat_error > 0.8 \
            and (ctx.positional_delta() != ctx.positional_delta() or ctx.positional_delta() < 15) \
            and _amp_low(ctx) < 0.3 and ctx.extra_peaks < 1.0:
        out.append(Signature(
            "Beat error only -- collet position",
            0.5,
            f"beat error {ctx.beat_error:.2f} ms with rate, amplitude and positions "
            f"otherwise healthy -- not a fault, just the hairspring collet or stud "
            f"carrier out of position",
            "Set the beat: rotate the stud carrier (Etachron / free-sprung) or the "
            "collet on the balance staff. No disassembly of the going train needed."))

    out.sort(key=lambda s: s.confidence, reverse=True)
    return [s for s in out if s.confidence >= 0.15]
