"""
Service templates.

A checklist for working through a movement: the phases in order, the
lubrication map, the specs worth having on the bench, and the failure points
that caliber is known for. A generic Swiss-lever template covers most of it;
a handful of common calibers add their own detail.

Nothing here is a substitute for the manufacturer's technical sheet -- it is
the working notes you would otherwise keep on an index card.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class ServiceTemplate:
    title: str
    phases: List[tuple] = field(default_factory=list)     # (phase name, [steps])
    lubrication: List[tuple] = field(default_factory=list)  # (point, lubricant)
    specs: List[tuple] = field(default_factory=list)        # (what, value)
    weak_points: List[str] = field(default_factory=list)
    note: str = ""


_GENERIC = ServiceTemplate(
    title="Generic Swiss lever service",
    phases=[
        ("Before teardown", [
            "Record rate, amplitude and beat error in all six positions, full wind",
            "Note power reserve if time allows, and any complaint from the owner",
            "Check case, crystal, crown and gaskets; photograph the dial and hands",
            "Demagnetise before measuring -- a magnetised reading misleads everything after",
        ]),
        ("Disassembly", [
            "Let the mainspring down fully through the click",
            "Remove automatic bridge / winding module, then balance, then pallet fork",
            "Remove train bridge and wheels, keep them in order",
            "Remove motion work, cannon pinion, keyless works",
            "Remove barrel bridge and barrel; open barrel, inspect spring and arbor",
        ]),
        ("Cleaning", [
            "Pre-clean shellac-set parts (pallet fork, balance) separately, short cycles",
            "Do not put shellac parts through hot or long ammoniated cycles",
            "Rinse and dry fully; inspect every pivot and jewel under magnification",
            "Peg every jewel hole; check for cracked or worn jewels",
        ]),
        ("Inspection", [
            "Check pivots for bends and polish wear; check endshakes dry-assembled",
            "Check mainspring for set (compare free coil diameter to barrel), replace if set",
            "Check escape teeth and pallet stones for chips and wear",
            "Check hairspring flat and centred, coils even, stud and regulator pin play",
        ]),
        ("Lubrication & assembly", [
            "Barrel wall braking grease (automatic) or barrel wall / arbor oil",
            "Train jewels 9010; centre wheel and slower wheels HP-1300",
            "Pallet stones / escape teeth: 9415 on the exit stone, run it in",
            "Cap jewels for balance and escape: 9010, epilame first if used",
            "Keyless works and setting parts: HP-1300 / molykote where sliding",
            "Reassemble, fit balance last, check it starts from a gentle puff",
        ]),
        ("Timing & closing", [
            "Wind fully, let settle 15-30 min, measure all six positions",
            "Regulate for the smallest positional spread, then centre the mean rate",
            "Set beat error under 0.3 ms",
            "Recheck after 24 h; case up; pressure-test water resistance",
        ]),
    ],
    lubrication=[
        ("Balance & escape cap jewels", "Moebius 9010"),
        ("Train wheel jewels (fast)", "Moebius 9010"),
        ("Centre wheel & slow wheels", "Moebius HP-1300"),
        ("Pallet exit stone / escape teeth", "Moebius 9415"),
        ("Barrel wall (automatic)", "Moebius 8217 braking grease"),
        ("Barrel arbor & wall (hand-wind)", "Moebius HP-1300 / D5"),
        ("Keyless works, sliding parts", "Moebius HP-1300 / Molykote DX"),
        ("Cannon pinion friction", "Moebius 9504 grease"),
    ],
    specs=[
        ("Balance endshake", "~0.02-0.04 mm, just perceptible"),
        ("Beat error target", "< 0.3 ms"),
        ("Amplitude, dial up, full wind", "260-310 deg typical modern"),
        ("Positional delta target", "< 15 s/day"),
    ],
    weak_points=[
        "A magnetised hairspring is the single most common 'runs fast' cause -- rule it out first",
        "Old braking grease going hard shows as low amplitude that recovers after service",
    ],
)


def _sw_auto(name, extra_weak=(), extra_specs=()):
    t = ServiceTemplate(
        title=f"{name} service", phases=list(_GENERIC.phases),
        lubrication=list(_GENERIC.lubrication), specs=list(_GENERIC.specs),
        weak_points=list(_GENERIC.weak_points))
    t.specs = t.specs + list(extra_specs)
    t.weak_points = t.weak_points + list(extra_weak)
    return t


_ETA_2824 = _sw_auto("ETA 2824-2 / Sellita SW200-1", extra_weak=[
    "Etachron regulator: set beat by rotating the stud carrier, not the collet",
    "Reversing wheels: clean, do not oil the pawls -- treat with epilame only",
    "Date jumper spring is easy to launch; fit it under a finger",
], extra_specs=[("Nominal", "28,800 bph, 25 j, lift 50 deg"),
                ("Amplitude spec", ">= 270 dial up full wind after service")])

_SW300 = _sw_auto("Sellita SW300-1 / ETA 2892-A2", extra_weak=[
    "Thin caliber, lower torque margin -- Sellita spec allows amplitude as low as 200; do not chase 300",
    "Fragile centre-seconds friction spring on the 2892",
], extra_specs=[("Nominal", "28,800 bph, 21 j, lift 51-52 deg")])

_VALJOUX_7750 = _sw_auto("ETA/Valjoux 7750 / Sellita SW500", extra_weak=[
    "Expect 20-40 deg amplitude drop with the chronograph running -- that is normal",
    "Oil the chronograph levers and hammer sparingly with HP-1300; cam faces with 9504",
    "Check the hammer heart-piece contact and the minute-counter jumper",
], extra_specs=[("Nominal", "28,800 bph, cam-switched chronograph, lift 49-50 deg")])

_UNITAS_6497 = _sw_auto("ETA/Unitas 6497 / 6498", extra_weak=[
    "Big slow balance, 18,000 bph -- amplitude in the 260s is fine",
    "Long thin mainspring; check carefully for set",
], extra_specs=[("Nominal", "18,000 bph (6497-1) or 21,600 (6497-2), lift 44-53 deg")])

_SEIKO_NH35 = ServiceTemplate(
    title="Seiko NH35 / NH36 / 4R35 service",
    phases=list(_GENERIC.phases),
    lubrication=list(_GENERIC.lubrication),
    specs=list(_GENERIC.specs) + [("Nominal", "21,600 bph, 24 j, lift ~53 deg"),
                                  ("Amplitude", "250-270 healthy; Seiko runs lower by design")],
    weak_points=list(_GENERIC.weak_points) + [
        "Has a moveable stud arm -- beat error IS adjustable, unlike the 7S26",
        "Diashock settings on balance and escape; magic-lever automatic works, do not oil the levers",
        "Parts are cheap -- a new mainspring and pallet fork often beats fettling an old one",
    ])

_SEIKO_7S26 = ServiceTemplate(
    title="Seiko 7S26 / 7S36 service",
    phases=list(_GENERIC.phases),
    lubrication=list(_GENERIC.lubrication),
    specs=list(_GENERIC.specs) + [("Nominal", "21,600 bph, 21 j, lift ~53 deg")],
    weak_points=list(_GENERIC.weak_points) + [
        "No hand-wind, no hacking, and beat error is NOT adjustable without moving the collet",
        "Factory beat error of 0.5-1.0 ms is common and acceptable on this caliber",
        "Magic-lever automatic; do not oil the pawl levers",
    ])

TEMPLATES = {
    "eta_2824_2": _ETA_2824, "eta_2836_2": _ETA_2824, "sw200_1": _ETA_2824,
    "eta_2892a2": _SW300, "sw300_1": _SW300,
    "eta_7750": _VALJOUX_7750, "sw500": _VALJOUX_7750,
    "eta_6497_1": _UNITAS_6497, "eta_6497_2": _UNITAS_6497,
    "seiko_nh35": _SEIKO_NH35, "seiko_6r15": _SEIKO_NH35,
    "seiko_7s26": _SEIKO_7S26,
}


def for_caliber(key: str = "", family: str = "") -> ServiceTemplate:
    if key and key in TEMPLATES:
        return TEMPLATES[key]
    k = (key or "").lower()
    for needle, tmpl in (("7750", _VALJOUX_7750), ("2824", _ETA_2824),
                         ("sw200", _ETA_2824), ("2892", _SW300), ("sw300", _SW300),
                         ("6497", _UNITAS_6497), ("6498", _UNITAS_6497),
                         ("nh35", _SEIKO_NH35), ("nh36", _SEIKO_NH35),
                         ("7s26", _SEIKO_7S26), ("7s36", _SEIKO_7S26)):
        if needle in k:
            return tmpl
    return _GENERIC


def render_markdown(t: ServiceTemplate, filled=None, header="") -> str:
    """`filled` optionally maps 'phase\tstep' -> bool for a completed checklist."""
    filled = filled or {}
    out = [f"# {header or t.title}", ""]
    for phase, steps in t.phases:
        out.append(f"## {phase}")
        for s in steps:
            box = "x" if filled.get(f"{phase}\t{s}") else " "
            out.append(f"- [{box}] {s}")
        out.append("")
    if t.lubrication:
        out.append("## Lubrication map")
        for point, oil in t.lubrication:
            out.append(f"- **{point}** -- {oil}")
        out.append("")
    if t.specs:
        out.append("## Specs")
        for k, v in t.specs:
            out.append(f"- {k}: {v}")
        out.append("")
    if t.weak_points:
        out.append("## Known weak points")
        for wp in t.weak_points:
            out.append(f"- {wp}")
        out.append("")
    return "\n".join(out)
