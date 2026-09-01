"""
Turns measurements into an ordered list of things to actually do.

The ordering is deliberate and matches bench practice:

  1. Trust the numbers first. A wrong lift angle or a bad pickup makes
     everything downstream meaningless.
  2. Amplitude. It is the energy budget of the whole movement. Regulating
     a watch with sick amplitude just bakes in a rate that will drift as
     soon as the amplitude moves.
  3. Beat error. Cheap to fix on most calibers and it destabilises rate
     across positions when it is large.
  4. Rate. Last, because steps 2 and 3 both move it.
  5. Positional delta and isochronism, which are poise and hairspring
     problems that no amount of regulating will cure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .calibers import Caliber, REGULATOR_LABELS

POSITIONS = ["Dial up", "Dial down", "Crown down", "Crown left", "Crown right", "Crown up"]
HORIZONTAL = {"Dial up", "Dial down"}


# --------------------------------------------------------------------------
# Timekeeping standards -- indicative grading from a hobby six-position run
# --------------------------------------------------------------------------

# A real COSC / METAS test runs 15+ days at three temperatures on a lab rig.
# These check the handful of criteria a bench six-position set can actually
# speak to: mean rate, positional spread, the dial-up/dial-down pair,
# amplitude and beat error. Treat a pass as "consistent with", not certified.
STANDARDS = {
    "COSC-style (indicative)":  dict(mean=(-4.0, 6.0), maxdev=10.0, dudd=8.0,
                                     amp_min=200.0, be_max=None),
    "METAS-style (0 / +5)":     dict(mean=(0.0, 5.0), maxdev=8.0, dudd=6.0,
                                     amp_min=220.0, be_max=0.6),
    "Manufacture typical":      dict(mean=(-5.0, 8.0), maxdev=15.0, dudd=12.0,
                                     amp_min=200.0, be_max=0.8),
    "Serviceable / vintage OK": dict(mean=(-20.0, 20.0), maxdev=30.0, dudd=25.0,
                                     amp_min=170.0, be_max=1.2),
}


@dataclass
class GradeRow:
    name: str
    value: str
    limit: str
    ok: bool


def grade(readings, spec) -> "tuple[bool, list]":
    """spec is one of STANDARDS' dicts (or a custom one of the same shape)."""
    rates = [r.rate for r in readings if r.rate == r.rate]
    if not rates:
        return False, [GradeRow("Position data", "none", "at least dial up", False)]
    mean = sum(rates) / len(rates)
    maxdev = max(abs(x - mean) for x in rates)
    lo, hi = spec["mean"]
    rows = [GradeRow("Mean daily rate", f"{mean:+.1f} s/d",
                     f"{lo:+.0f} to {hi:+.0f}", lo <= mean <= hi)]
    if len(rates) >= 2 and spec.get("maxdev") is not None:
        rows.append(GradeRow("Largest deviation from mean", f"{maxdev:.1f} s/d",
                             f"<= {spec['maxdev']:.0f}", maxdev <= spec["maxdev"]))
    du = next((r.rate for r in readings if r.position == "Dial up"), None)
    dd = next((r.rate for r in readings if r.position == "Dial down"), None)
    if du is not None and dd is not None and spec.get("dudd") is not None:
        rows.append(GradeRow("Dial up vs dial down", f"{abs(du - dd):.1f} s/d",
                             f"<= {spec['dudd']:.0f}", abs(du - dd) <= spec["dudd"]))
    amps = [r.amplitude for r in readings if r.amplitude == r.amplitude]
    if amps and spec.get("amp_min") is not None:
        rows.append(GradeRow("Lowest amplitude", f"{min(amps):.0f} deg",
                             f">= {spec['amp_min']:.0f}", min(amps) >= spec["amp_min"]))
    bes = [r.beat_error for r in readings if r.beat_error == r.beat_error]
    if bes and spec.get("be_max") is not None:
        rows.append(GradeRow("Beat error (worst)", f"{max(bes):.2f} ms",
                             f"<= {spec['be_max']:.2f}", max(bes) <= spec["be_max"]))
    return all(r.ok for r in rows), rows


@dataclass
class Reading:
    position: str = "Dial up"
    rate: float = float("nan")
    amplitude: float = float("nan")
    beat_error: float = float("nan")
    wind_state: str = "Full wind"     # "Full wind" or "24h" etc.
    note: str = ""


@dataclass
class Finding:
    severity: str      # "critical" | "warn" | "info" | "good"
    title: str
    detail: str


SEV_ORDER = {"critical": 0, "warn": 1, "info": 2, "good": 3}


# --------------------------------------------------------------------------
# Beat error / rate adjustment text, keyed to the hardware
# --------------------------------------------------------------------------

def beat_adjust_instructions(cal: Caliber) -> str:
    r = cal.regulator
    if r == "etachron":
        return (
            "This caliber has an Etachron stud carrier. With the balance in place, "
            "rotate the stud carrier (the small arm holding the hairspring stud, not "
            "the regulator arm) in tiny increments and re-measure after each nudge. "
            "Rotating it moves the balance's rest position relative to the pallet fork. "
            "If you overshoot, the beat error climbs again on the other side, so bracket "
            "it: note the direction that reduced the number and halve your step size."
        )
    if r == "index_stud":
        return (
            "This caliber has a moveable stud arm on the balance cock. Nudge it with "
            "tweezers or a proper stud-arm tool in small steps and re-measure. Do not "
            "confuse it with the regulator arm sitting next to it -- moving the wrong "
            "one changes rate, not beat."
        )
    if r == "freesprung":
        return (
            "Free-sprung caliber. Beat error is set by the moveable stud carrier on the "
            "balance bridge. On Rolex and Omega this needs the brand-specific tool and a "
            "very light touch; the carrier is friction-fit and easy to distort."
        )
    if r == "swan_neck":
        return (
            "Beat error here is not the swan-neck screw -- that is fine rate adjustment. "
            "Beat requires either a moveable stud (check the balance cock) or rotating "
            "the hairspring collet on the balance staff."
        )
    return (
        "This caliber has no moveable stud. Correcting beat error means removing the "
        "balance and rotating the hairspring collet on the staff with a collet-turning "
        "tool. That is a real operation with real risk of bending the hairspring -- if "
        "the beat error is under about 0.8ms and the watch keeps good time, the sensible "
        "call is to leave it alone until the next service."
    )


def rate_adjust_instructions(cal: Caliber, direction: str) -> str:
    """direction is 'faster' or 'slower'."""
    r = cal.regulator
    if r == "freesprung":
        inward = "inward (toward the balance staff)" if direction == "faster" else "outward (away from the staff)"
        return (
            f"Free-sprung: there is no regulator to move. Rate is set by the inertia "
            f"weights on the balance rim -- Microstella nuts on Rolex, mass screws on "
            f"Omega/Tudor/GS. To run {direction}, move the weights {inward}, the same "
            f"physics as a spinning skater pulling their arms in. Turn opposing pairs by "
            f"the same amount or you will poise the balance out and open up your positional "
            f"delta. This needs the correct fork tool; a screwdriver will chew the nuts."
        )
    if r == "swan_neck":
        sign = "+" if direction == "faster" else "-"
        return (
            f"Turn the swan-neck fine adjustment screw to walk the index toward {sign}. "
            f"That is what the swan-neck is for -- do not push the index arm directly "
            f"while the swan-neck spring is engaged."
        )
    sign = "+ (or A / Avance)" if direction == "faster" else "- (or R / Retard)"
    extra = ""
    if r == "etachron":
        extra = (
            " On Etachron, also check the regulator boot: the gap between the two pins "
            "and the hairspring sets how much positional variation you get. Wide pins "
            "give a large delta between horizontal and vertical positions. Set the boot "
            "so the hairspring has roughly one hairspring-thickness of play, then regulate."
        )
    return (
        f"Move the regulator arm on the balance cock toward {sign}. Use a fine tool "
        f"against the arm itself, not the hairspring, and move it in very small "
        f"increments -- on a 4 Hz caliber the whole usable index travel is often only "
        f"a couple of millimetres for hundreds of seconds a day.{extra}"
    )


def regulator_sensitivity(before_rate: float, after_rate: float, step_desc: str = "that move"):
    """
    After one deliberate adjustment, this tells you how much further to go.
    Beats guessing: index sensitivity varies enormously between calibers.
    """
    delta = after_rate - before_rate
    if abs(delta) < 0.5:
        return ("The rate barely moved. Either the adjustment did not take, or you are "
                "fighting friction in the regulator. Check the index is actually moving "
                "and not just flexing.")
    remaining = -after_rate
    fraction = remaining / delta
    if abs(fraction) < 0.05:
        return "You are there. Leave it alone."
    direction = "the same direction again" if fraction > 0 else "back the other way"
    return (f"{step_desc} moved the rate by {delta:+.1f} s/day. To land on zero you need "
            f"{remaining:+.1f} s/day more, which is about {abs(fraction):.2f}x that step, "
            f"{direction}.")


# --------------------------------------------------------------------------
# Main analysis
# --------------------------------------------------------------------------

def _amp_expectation(cal: Caliber, position: str, wind: str):
    lo, hi = cal.amp_full_wind
    if position not in HORIZONTAL:
        lo, hi = lo - 40, hi - 20      # verticals always sit lower
    if wind and wind != "Full wind":
        lo, hi = lo - 40, hi - 20
    return lo, hi


def diagnose(cal: Caliber, readings, measured_lift: Optional[float] = None,
             detected_bph: Optional[int] = None, quality: Optional[float] = None,
             amplitude_spread: Optional[float] = None):
    """
    readings: list of Reading. A single dial-up reading is enough for basic
    advice; six positions unlock the delta and poise checks.
    """
    out = []
    rs = [r for r in readings if r.position]
    if not rs:
        return [Finding("info", "No readings yet",
                        "Take at least one measurement, dial up, at full wind.")]

    # ---- 0. Can we trust the numbers? ------------------------------------
    if detected_bph and cal.bph and detected_bph != cal.bph:
        out.append(Finding(
            "critical", f"Detected {detected_bph} bph, but {cal.label} runs at {cal.bph} bph",
            "Either the caliber selection is wrong, or the pickup is mistracking. Every "
            "number below is computed against the nominal frequency, so fix this first. "
            "If the watch genuinely runs at the detected rate, you may have a replacement "
            "or swapped balance."))

    src = getattr(cal, "lift_source", "community")
    if src == "inherited":
        out.append(Finding(
            "info", f"Lift angle {cal.lift_angle:.1f} deg for {cal.label} is inherited, not measured",
            "Nobody has published a lift angle for this caliber. This figure comes from the "
            "movement it clones, on the reasoning that a clone copies the escapement "
            "geometry -- which is usually true and occasionally not. Rate and beat error are "
            "unaffected. Amplitude could be off by 10-20 degrees, and 1 degree of lift angle "
            "error is worth about 5 degrees of amplitude. If you care about the amplitude "
            "number on this movement, solve for the real lift angle using the 180-degree "
            "method in the Tools tab."))
    elif src == "community":
        out.append(Finding(
            "info", f"Lift angle {cal.lift_angle:.1f} deg for {cal.label} is unconfirmed",
            "Community consensus rather than a manufacturer figure. Rate and beat error are "
            "unaffected, but amplitude could be off by 10-20 degrees."))

    if quality is not None and quality < 0.75:
        out.append(Finding(
            "warn", f"Signal quality is low ({quality:.2f})",
            "Beat waveforms are not matching each other well. Improve acoustic coupling -- "
            "press the case back firmly against the pickup, kill background noise, and "
            "check the microphone is not clipping."))

    if amplitude_spread is not None and amplitude_spread > 25:
        out.append(Finding(
            "warn", f"Amplitude scatter is high (+/-{amplitude_spread/2:.0f} deg beat to beat)",
            "Individual beats disagree about the amplitude. Usually a noisy pickup, but if "
            "the signal is otherwise clean it points at an intermittent escapement fault: "
            "a chipped or loose pallet stone, a bent escape wheel tooth, or debris."))

    # ---- 1. Amplitude ----------------------------------------------------
    amps = [(r, r.amplitude) for r in rs if r.amplitude == r.amplitude]
    if amps:
        worst = min(amps, key=lambda p: p[1])
        best = max(amps, key=lambda p: p[1])
        lo, hi = _amp_expectation(cal, best[0].position, best[0].wind_state)

        if best[1] > 330:
            out.append(Finding(
                "critical", f"Amplitude {best[1]:.0f} deg -- knocking territory",
                "Above roughly 330 degrees the impulse pin starts hitting the back of the "
                "pallet fork horn (rebanking/knocking). The watch will run wildly fast and "
                "the escapement takes real damage. Usual causes: a mainspring that is too "
                "strong for the caliber, too little end-shake or oil at the escapement, or "
                "a balance that is too light. Do not regulate this -- find the cause."))
        elif best[1] < lo - 45:
            out.append(Finding(
                "critical", f"Amplitude {best[1]:.0f} deg is far below the {lo:.0f}-{hi:.0f} "
                f"expected for {cal.label}",
                "This is a service case, not a regulation case. In rough order of likelihood: "
                "dried or migrated lubricant on the pallet stones and escape wheel; a tired or "
                "set mainspring; dirt in the train; a bent pivot or a rubbing hairspring; "
                "magnetism. Regulating now buys you a rate that will not hold."))
        elif best[1] < lo:
            out.append(Finding(
                "warn", f"Amplitude {best[1]:.0f} deg is below the {lo:.0f}-{hi:.0f} expected "
                f"for {cal.label}",
                "The movement is running but the energy budget is thin -- typically 4-6 years "
                "past a service. Check it again at 24 hours off full wind; if it falls below "
                "about 200 the watch will start losing its grip on rate and positional "
                "stability. First cheap checks: demagnetise, and confirm the mainspring is "
                "the correct strength."))
        else:
            out.append(Finding(
                "good", f"Amplitude {best[1]:.0f} deg is healthy for {cal.label}",
                f"Expected range for this caliber in this position and wind state is "
                f"{lo:.0f}-{hi:.0f} degrees."))

        if len(amps) >= 4 and (best[1] - worst[1]) > 60:
            out.append(Finding(
                "warn", f"Amplitude drops {best[1]-worst[1]:.0f} deg from {best[0].position} "
                f"to {worst[0].position}",
                "A drop of 20-50 degrees from horizontal to vertical is normal -- the balance "
                "pivots carry the wheel's weight on their sides. More than about 60 degrees "
                "suggests worn or dirty balance pivots, a damaged jewel, or a hairspring "
                "touching something when the watch is on edge."))

    # ---- 2. Beat error ---------------------------------------------------
    bes = [r.beat_error for r in rs if r.beat_error == r.beat_error]
    if bes:
        be = max(bes)
        if be > 1.2:
            out.append(Finding(
                "critical", f"Beat error {be:.2f} ms",
                "The balance is noticeably off centre, so one half of the swing gets its "
                "impulse earlier than the other. Beyond about 1.2ms it costs you amplitude, "
                "destabilises rate across positions, and can make the watch refuse to "
                "self-start after it stops. " + beat_adjust_instructions(cal)))
        elif be > 0.6:
            out.append(Finding(
                "warn", f"Beat error {be:.2f} ms",
                "Not urgent, but worth correcting -- aim under 0.5ms, ideally under 0.3. "
                + beat_adjust_instructions(cal)))
        else:
            out.append(Finding(
                "good", f"Beat error {be:.2f} ms is fine",
                "Under 0.5ms is good, under 0.3ms is what a careful regulation looks like. "
                "Chasing the last 0.1ms is not worth disturbing a healthy movement for."))

    # ---- 3. Rate ---------------------------------------------------------
    rates = [r.rate for r in rs if r.rate == r.rate]
    if rates:
        mean_rate = sum(rates) / len(rates)
        blocked = any(f.severity == "critical" and "Amplitude" in f.title for f in out)
        if abs(mean_rate) <= 4:
            out.append(Finding(
                "good", f"Rate averages {mean_rate:+.1f} s/day across the positions measured",
                "That is chronometer-grade territory. Leave the regulator alone."))
        elif abs(mean_rate) <= 12:
            direction = "slower" if mean_rate > 0 else "faster"
            out.append(Finding(
                "info", f"Rate averages {mean_rate:+.1f} s/day -- correctable by regulation",
                f"To bring this to zero you need the watch to run {direction}. "
                + rate_adjust_instructions(cal, direction)))
        else:
            direction = "slower" if mean_rate > 0 else "faster"
            msg = (f"To bring this to zero you need the watch to run {direction}. "
                   + rate_adjust_instructions(cal, direction))
            if blocked:
                msg = ("Fix the amplitude problem above BEFORE touching the regulator. "
                       "Rate and amplitude are coupled -- a movement that gains amplitude "
                       "after service will land somewhere else entirely. " + msg)
            out.append(Finding("warn", f"Rate averages {mean_rate:+.1f} s/day", msg))

        if len(rates) >= 4:
            delta = max(rates) - min(rates)
            fastest = max(rs, key=lambda r: r.rate if r.rate == r.rate else -1e9)
            slowest = min(rs, key=lambda r: r.rate if r.rate == r.rate else 1e9)
            if delta <= 10:
                out.append(Finding(
                    "good", f"Positional delta {delta:.1f} s/day",
                    "Under about 10 s/day across positions is a well-adjusted movement."))
            elif delta <= 25:
                out.append(Finding(
                    "info", f"Positional delta {delta:.1f} s/day "
                    f"({fastest.position} fastest, {slowest.position} slowest)",
                    "Ordinary for a non-chronometer caliber. If you want to shrink it, the "
                    "usual levers are the regulator pin gap (on Etachron), hairspring "
                    "centring and flatness, and balance poise -- in that order of effort. "
                    "A practical trick: regulate so the two positions you actually wear the "
                    "watch in average to zero, rather than optimising dial-up."))
            else:
                out.append(Finding(
                    "warn", f"Positional delta {delta:.1f} s/day "
                    f"({fastest.position} fastest, {slowest.position} slowest)",
                    "This is too large to regulate away -- you can only move the whole set up "
                    "or down. A big vertical-vs-horizontal split points at hairspring "
                    "problems: not flat, not concentric, or rubbing the regulator pins or "
                    "the balance cock. A big split between the vertical positions themselves "
                    "points at balance poise or a bent pivot."))

    # ---- 4. Isochronism --------------------------------------------------
    by_wind = {}
    for r in rs:
        if r.rate == r.rate:
            by_wind.setdefault(r.wind_state, []).append(r.rate)
    if len(by_wind) >= 2:
        full = by_wind.get("Full wind")
        others = {k: v for k, v in by_wind.items() if k != "Full wind"}
        if full and others:
            k, v = next(iter(others.items()))
            drift = sum(v) / len(v) - sum(full) / len(full)
            if abs(drift) > 12:
                out.append(Finding(
                    "warn", f"Rate moves {drift:+.1f} s/day between full wind and {k}",
                    "Poor isochronism. The hairspring is not developing evenly -- most often "
                    "the terminal curve or the regulator pin gap. It can also be a mainspring "
                    "delivering an uneven torque curve, or a barrel arbor problem."))
            else:
                out.append(Finding(
                    "good", f"Isochronism holds to {drift:+.1f} s/day from full wind to {k}",
                    "The rate is not drifting much as the mainspring unwinds."))

    out.sort(key=lambda f: SEV_ORDER.get(f.severity, 9))
    return out


def workflow_summary(cal: Caliber) -> str:
    return (
        f"{cal.label} -- {cal.bph if cal.bph else 'beat rate auto-detected'}"
        f"{' bph' if cal.bph else ''}, lift angle {cal.lift_angle:.1f} deg\n"
        f"Regulating hardware: {REGULATOR_LABELS.get(cal.regulator, cal.regulator)}\n\n"
        "Suggested bench order:\n"
        "  1. Wind fully, let it settle 10-15 minutes, then measure dial up.\n"
        "  2. Confirm the detected bph matches the caliber before reading anything else.\n"
        "  3. Sort out amplitude first. It is the energy budget for everything else.\n"
        "  4. Then beat error. Then rate. Regulating first just wastes the work.\n"
        "  5. Measure all six positions, then re-check at 24 hours off full wind.\n"
        + (f"\nCaliber notes: {cal.notes}\n" if cal.notes else "")
    )
