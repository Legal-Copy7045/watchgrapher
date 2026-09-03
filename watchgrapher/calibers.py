"""
Caliber database: beat rate, lift angle, and the regulating hardware
each movement actually gives you to work with.

Lift angle does NOT affect rate or beat error. It only scales amplitude.
Getting it wrong by 1 degree shifts amplitude by roughly 5 degrees, so it
is worth looking up rather than leaving at the 52 default.

`verified` flags whether the lift angle comes from a manufacturer/technical
sheet or a well-corroborated reference list. Entries marked False are
community consensus values -- treat the amplitude number as indicative.

Regulator types drive the advice engine:
  etachron   - ETA/Sellita style: regulator arm + rotatable stud carrier
               and rotatable regulator boot. Beat set by turning stud carrier.
  index      - Plain index/regulator arm, hairspring stud fixed.
               Beat error requires rotating the collet on the balance staff.
  index_stud - Index arm plus a moveable (screw or friction) stud arm.
               Beat adjustable without removing the balance.
  freesprung - No index. Rate set by inertia weights on the balance rim
               (Microstella, Omega mass screws, GS, Tudor). Beat set by
               a moveable stud carrier.
  swan_neck  - Index arm with a swan-neck fine adjuster screw.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Optional

# Frequencies a mechanical watch is actually likely to run at.
STANDARD_BPH = [12000, 14400, 16200, 18000, 19800, 21600, 25200, 28800, 36000, 43200]

DEFAULT_LIFT_ANGLE = 52.0


@dataclass
class Caliber:
    key: str
    brand: str
    name: str
    # None means the source did not publish a beat rate. The analyzer then
    # stays in auto-detect, which is what it does well anyway.
    bph: Optional[int]
    lift_angle: float
    regulator: str = "index"
    verified: bool = True
    # Where the lift angle came from. Matters most for Chinese clones, where
    # manufacturer documentation frequently does not exist at all:
    #   documented - manufacturer or technical sheet
    #   measured   - published bench measurement on real samples
    #   community  - corroborated enthusiast consensus
    #   inherited  - taken from the caliber this one clones, because clones
    #                copy the escapement geometry. Best available estimate,
    #                but nobody has actually measured this one.
    lift_source: str = "documented"
    # Display grouping, so a 1950s ebauche and a Rolex 3235 do not sit next to
    # each other in one flat 2000-entry list.
    group: str = "Swiss"
    # Escape wheel teeth. Sets the expected period of an escape-wheel fault:
    # one revolution spans 2 x teeth beats, since each tooth is released once
    # by the entry stone and once by the exit. 15 is the Swiss lever norm.
    escape_teeth: int = 15
    # Manufacturer / practical amplitude expectation, dial-up, full wind.
    amp_full_wind: tuple = (250.0, 315.0)
    # Optional gear-train periods, {wheel name: seconds per revolution}, for the
    # periodic fault scan. None -> the fourth wheel is assumed to carry the
    # seconds hand (60 s) and the third wheel is estimated.
    train: Optional[dict] = None
    # "What's normal" reference.
    service_interval_years: int = 5
    power_reserve_h: float = 0.0        # 0 = unknown
    jewels: int = 0                     # 0 = unknown
    known_issues: str = ""
    notes: str = ""

    @property
    def label(self) -> str:
        return f"{self.brand} {self.name}"

    @property
    def free_sprung(self) -> bool:
        return self.regulator == "freesprung"


SWISS_MODERN = {"ETA", "Sellita", "Rolex", "Tudor", "Omega", "IWC", "Jaeger-LeCoultre",
                "Lemania", "Nomos", "Frederic Piguet", "Girard-Perregaux", "Zenith",
                "Panerai", "Eterna", "Blancpain", "Cartier", "Oris", "ETA/Valjoux",
                "ETA/Unitas", "ETA/Peseux", "Landeron"}
JAPANESE = {"Seiko", "Grand Seiko", "Seiko/SII", "Miyota", "Orient", "Citizen"}
CHINESE_CLONE = {"Sea-Gull", "Peacock (Dandong)", "HKPT", "PTS / Dixmont", "Shanghai",
                 "Sunon", "Beijing", "Tianjin"}
CHINESE_ORIG = {"Hangzhou"}
RUSSIAN = {"Poljot", "Raketa", "Vostok", "Molnija", "Luch", "Chaika", "Pobeda"}


def _auto_group(brand: str) -> str:
    if brand in CHINESE_CLONE:
        return "Chinese - clones"
    if brand in CHINESE_ORIG:
        return "Chinese - in-house"
    if brand in JAPANESE:
        return "Japanese"
    if brand in RUSSIAN:
        return "Russian / Eastern Bloc"
    if brand == "Generic":
        return "Generic fallbacks"
    if brand in SWISS_MODERN:
        return "Swiss / European"
    return "Swiss / European"


def _c(key, brand, name, bph, lift, reg="index", verified=True, amp=(250, 315),
       notes="", src=None, group=None, teeth=15):
    if src is None:
        src = "documented" if verified else "community"
    return Caliber(key, brand, name, bph, float(lift), reg, verified,
                   src, group or _auto_group(brand), teeth, amp, notes=notes)


_LIST = [
    # ---- ETA / Swatch Group ----------------------------------------------
    _c("eta_2824_2", "ETA", "2824-2", 28800, 50, "etachron", True, (270, 315),
       "Workhorse automatic. Etachron regulator; beat set by rotating the stud carrier."),
    _c("eta_2836_2", "ETA", "2836-2", 28800, 50, "etachron", True, (270, 315),
       "2824-2 with day/date. Same escapement geometry."),
    _c("eta_2892a2", "ETA", "2892-A2", 28800, 52, "etachron", True, (270, 315),
       "Thin automatic. Slightly lower torque reserve than 2824."),
    _c("eta_2893_2", "ETA", "2893-2", 28800, 52, "etachron", True, (265, 310),
       "2892 base with GMT module."),
    _c("eta_7750", "ETA/Valjoux", "7750", 28800, 49, "etachron", True, (270, 315),
       "Chronograph. Expect 20-40 deg amplitude drop when the chrono is running."),
    _c("eta_2894_2", "ETA", "2894-2", 28800, 50, "etachron", True, (250, 300)),
    _c("eta_6497_1", "ETA/Unitas", "6497-1 / 6498-1", 18000, 44, "etachron", True, (260, 310),
       "Large pocket-watch caliber. Low beat, big balance."),
    _c("eta_6497_2", "ETA/Unitas", "6497-2 / 6498-2", 21600, 44, "etachron", True, (260, 310)),
    _c("eta_7001", "ETA/Peseux", "7001", 21600, 50, "etachron", True, (240, 300),
       "Thin hand-wind. Amplitude in the 240s is normal here."),
    _c("eta_2000_1", "ETA", "2000-1", 28800, 50, "etachron", True, (250, 300)),
    _c("eta_c07", "ETA", "C07.111 / C07.611 (Powermatic 80)", 21600, 50, "index", True, (240, 290),
       "80h reserve, Nivachron. Many are laser-regulated with a sealed/limited index."),
    _c("eta_a31", "ETA", "A31.L01 (Longines L888)", 25200, 50, "etachron", False, (250, 300)),

    # ---- Sellita ----------------------------------------------------------
    _c("sw200_1", "Sellita", "SW200-1", 28800, 50, "etachron", True, (270, 315),
       "2824-2 equivalent."),
    _c("sw300_1", "Sellita", "SW300-1", 28800, 51, "etachron", True, (200, 290),
       "2892 equivalent. Sellita's own spec allows amplitude as low as ~200 at full wind -- "
       "do not chase 300 here."),
    _c("sw500", "Sellita", "SW500", 28800, 49, "etachron", True, (260, 310),
       "7750 equivalent."),
    _c("sw240", "Sellita", "SW240-1", 21600, 50, "etachron", False, (250, 300)),

    # ---- Seiko / Seiko Instruments (NH) -----------------------------------
    _c("seiko_7s26", "Seiko", "7S26 / 7S36", 21600, 53, "index", True, (230, 290),
       "No hand-wind, no hacking. Beat error is not adjustable without moving the "
       "hairspring collet -- factory beat error of 0.5-1.0ms is common and normal."),
    _c("seiko_nh35", "Seiko/SII", "NH35 / NH36 / 4R35 / 4R36", 21600, 53, "index_stud", True, (230, 290),
       "Has a moveable stud arm, so beat error IS adjustable. Amplitude of 250-270 is healthy."),
    _c("seiko_6r15", "Seiko", "6R15 / 6R35", 21600, 53, "index_stud", True, (240, 290)),
    _c("seiko_6r55", "Seiko", "6R55 / 6R64", 21600, 53, "index_stud", False, (240, 290)),
    _c("seiko_9s65", "Grand Seiko", "9S65 / 9S68", 28800, 52, "freesprung", False, (270, 310),
       "Free-sprung with MEMS escapement. Rate via inertia weights; leave this to a GS-trained "
       "watchmaker unless you are equipped for it."),
    _c("seiko_9s85", "Grand Seiko", "9S85 (Hi-Beat)", 36000, 52, "freesprung", False, (260, 300)),
    _c("seiko_6139", "Seiko", "6139", 21600, 52, "index", True, (230, 280)),
    _c("seiko_6105", "Seiko", "6105 / 6106", 21600, 52, "index", False, (230, 280)),
    _c("seiko_7009", "Seiko", "7009 / 7019", 21600, 52, "index", False, (220, 280)),
    _c("seiko_8346", "Seiko", "8346A (Business-A)", 18000, 46, "index", False, (230, 280)),

    # ---- Miyota / Citizen -------------------------------------------------
    _c("miyota_8215", "Miyota", "8215 / 821A", 21600, 49, "index", True, (230, 280),
       "Budget automatic. Stuttering seconds hand is normal. No stud arm -- beat error "
       "is what it is unless you move the collet."),
    _c("miyota_9015", "Miyota", "9015 / 9039", 28800, 51, "index", True, (250, 300)),
    _c("miyota_90s5", "Miyota", "90S5 / 9100", 28800, 51, "index", True, (250, 300)),
    _c("miyota_82s7", "Miyota", "82S7 (open heart)", 21600, 49, "index", False, (220, 275)),

    # ---- Rolex / Tudor ----------------------------------------------------
    _c("rolex_3135", "Rolex", "3135", 28800, 52, "freesprung", True, (270, 315),
       "Free-sprung Glucydur balance with 4 Microstella nuts. Turning the nuts IN speeds the "
       "watch up. Beat via the moveable stud carrier. Needs Rolex-specific tools."),
    _c("rolex_3235", "Rolex", "3235 / 3230", 28800, 52, "freesprung", True, (270, 315),
       "Chronergy escapement, 70h reserve. Rate via variable-inertia weights."),
    _c("rolex_3130", "Rolex", "3130 / 3132", 28800, 52, "freesprung", True, (270, 315)),
    _c("rolex_3186", "Rolex", "3186 / 3285 (GMT)", 28800, 52, "freesprung", True, (265, 310)),
    _c("rolex_1570", "Rolex", "1570 / 1575", 18000, 52, "freesprung", True, (250, 300),
       "Vintage. Microstella, but often fitted with a regulator on early variants."),
    _c("rolex_2235", "Rolex", "2235 / 2236", 28800, 52, "freesprung", True, (260, 300)),
    _c("rolex_4130", "Rolex", "4130 (Daytona)", 28800, 52, "freesprung", True, (270, 310)),
    _c("tudor_mt5602", "Tudor", "MT5602 / MT5612 / MT5652", 28800, 49, "freesprung", False, (270, 315),
       "In-house, free-sprung, silicon hairspring, COSC. Weights only -- no index. "
       "Tudor does not publish a lift angle; 49 is the service-centre / enthusiast "
       "consensus (using the 52 default reads amplitude ~15 deg high).", "community"),
    _c("tudor_2824", "Tudor", "ETA 2824-2 based (pre-MT)", 28800, 50, "etachron", True, (270, 315)),

    # ---- Omega ------------------------------------------------------------
    _c("omega_1120", "Omega", "1120 (ETA 2892 base)", 28800, 51, "etachron", True, (270, 310)),
    _c("omega_1861", "Omega", "1861 / 861 / 1863", 21600, 50, "index", True, (250, 300),
       "Speedmaster Moonwatch. Hand-wind chronograph, swan-neck on some variants."),
    _c("omega_321", "Omega", "321", 18000, 40, "swan_neck", True, (240, 300),
       "Column wheel Speedmaster. Note the unusually low 40 deg lift angle."),
    _c("omega_565", "Omega", "550 / 561 / 564 / 565", 19800, 49, "swan_neck", True, (250, 300),
       "Classic 19,800 bph automatic family."),
    _c("omega_2500", "Omega", "2500 Co-Axial", 25200, 38, "index", True, (250, 300),
       "Co-Axial escapement. Lift angle is 38 -- using 52 will read ~90 deg too high.",
       None, None, 21),
    _c("omega_8500", "Omega", "8500 / 8900 / 8800 Co-Axial", 25200, 38, "freesprung", True, (250, 300),
       "Free-sprung, silicon Si14 hairspring, Master Chronometer on 88xx/89xx."),
    _c("omega_1152", "Omega", "1151 / 1152 (7750 base)", 28800, 49, "etachron", True, (260, 305)),
    _c("omega_1030", "Omega", "1010 / 1020 / 1030", 28800, 52, "index", True, (250, 300)),

    # ---- Other Swiss ------------------------------------------------------
    _c("jlc_889", "Jaeger-LeCoultre", "889 / 899", 28800, 50, "freesprung", False, (260, 305)),
    _c("jlc_938", "Jaeger-LeCoultre", "938 / 938A (Master Ultra Thin Power Reserve)", 28800, 50,
       "freesprung", False, (250, 300),
       "Thin automatic with power-reserve indication, 43h, 41 jewels, ~273 parts. "
       "Free-sprung balance adjusted by four rim screws (hairspring laser-welded, no "
       "index), ceramic rotor bearings, unidirectional winding. Derived from the 899 "
       "family -- JLC does not publish a lift angle, so 50 is carried over from the "
       "899 (jlc_889). Powers the Master Ultra Thin Reserve de Marche / Power Reserve "
       "and Master Control Power Reserve.", "inherited"),
    _c("jlc_920", "Jaeger-LeCoultre", "920 / AP 2120", 19800, 56, "freesprung", True, (250, 300),
       "Ultra-thin automatic. Lift angle 56."),
    _c("iwc_79350", "IWC", "79350 (7750 base)", 28800, 49, "etachron", True, (260, 310)),
    _c("iwc_30110", "IWC", "30110 (2892 base)", 28800, 52, "etachron", True, (265, 310)),
    _c("iwc_52010", "IWC", "52010 / 52610 Pellaton", 28800, 52, "freesprung", False, (260, 305)),
    _c("lemania_1873", "Lemania", "1873 / 873", 21600, 50, "index", True, (250, 300)),
    _c("lemania_5100", "Lemania", "5100", 28800, 53, "index", True, (240, 290)),
    _c("landeron_48", "Landeron", "48 / 51 / 149", 18000, 42, "index", True, (230, 290)),
    _c("nomos_alpha", "Nomos", "Alpha / DUW 4101", 21600, 52, "swan_neck", True, (250, 300)),
    _c("fp_1150", "Frederic Piguet", "1150 / 1151", 21600, 53, "index", True, (250, 300)),
    _c("gp_3300", "Girard-Perregaux", "3300", 28800, 52, "index", False, (255, 305)),
    _c("zenith_400", "Zenith", "400 / 4021 El Primero", 36000, 50, "index", False, (250, 300),
       "36,000 bph. High-beat movements normally sit 15-30 deg lower on amplitude than a 4Hz "
       "caliber -- 260 is healthy here."),
    _c("panerai_p3000", "Panerai", "P.3000 / P.3001", 21600, 50, "freesprung", False, (250, 300)),
    _c("eterna_3902", "Eterna", "3902A", 28800, 52, "index", False, (250, 300)),
    _c("blancpain_1151", "Blancpain", "1151", 21600, 53, "index", True, (250, 300)),
    _c("cartier_1904", "Cartier", "1904 MC", 28800, 52, "index", False, (255, 305)),
    _c("oris_400", "Oris", "Calibre 400", 21600, 52, "freesprung", False, (250, 300)),

    # ---- Russian / Chinese / Japanese other -------------------------------
    _c("poljot_3133", "Poljot", "3133", 21600, 51, "index", True, (230, 285)),
    _c("poljot_2609", "Poljot", "2609 / 2614", 21600, 51, "index", True, (230, 285)),
    _c("raketa_2609", "Raketa", "2609 / 26xx", 18000, 42, "index", True, (220, 280)),
    _c("vostok_2409", "Vostok", "2409 / 2414", 19800, 42, "index", False, (200, 270),
       "Amplitude in the 220s is typical and acceptable for these."),
    _c("vostok_2416", "Vostok", "2416B / 2426", 19800, 42, "index", False, (200, 270)),
    _c("st19", "Sea-Gull", "ST19 (Venus 175 base)", 21600, 45, "index", False, (230, 285)),
    _c("st16", "Sea-Gull", "ST1612 / ST16", 21600, 50, "index", False, (230, 285)),
    _c("hangzhou_6300", "Hangzhou", "6300", 28800, 52, "index", False, (240, 290)),
    _c("orient_f6922", "Orient", "F6922 / F6722", 21600, 53, "index_stud", False, (230, 285)),

    # ---- Sea-Gull / Tianjin (TY prefix = Tianjin) -------------------------
    _c("st2130", "Sea-Gull", "ST2130 / TY2130", 28800, 50, "etachron", False, (250, 300),
       "Near 1:1 ETA 2824-2 clone and the most common Chinese automatic in mod builds. "
       "Sold unmarked, so what arrives may or may not be a genuine Tianjin unit -- "
       "confirm the regulating hardware on the bench before adjusting.", "community"),
    _c("st3600", "Sea-Gull", "ST3600 / ST36", 21600, 44, "etachron", False, (240, 295),
       "ETA 6497-1 clone, the big hand-wind used in most Chinese pilot watches. "
       "Note it runs at 21600, not the 18000 of the original 6497-1.", "inherited"),
    _c("st2505", "Sea-Gull", "ST2505 / ST25 series", 28800, 50, "etachron", False, (240, 295),
       "ST21/ST25 family, 2824-derived.", "inherited"),

    # ---- Peacock / Dandong (Liaoning Peacock, Dandong Watch Industrial Park)
    _c("peacock_sl3000", "Peacock (Dandong)", "SL3000 / SL3001", 28800, 51.5, "etachron", False,
       (260, 310), "ETA 2824-2 clone and the base of the whole SL3 family. Widely regarded "
       "as the best-finished of the Chinese 2824 clones.", "inherited"),
    _c("peacock_sl3006", "Peacock (Dandong)", "SL3006", 28800, 51.5, "etachron", False, (265, 310),
       "32-jewel premium SL3000 derivative, factory-tested to +/-10 s/day. Used by "
       "Atelier Wen among others.", "inherited"),
    _c("peacock_sl3032", "Peacock (Dandong)", "SL3032", 28800, 51.5, "etachron", False, (260, 305),
       "SL3001 base with caller-GMT. Same escapement as the rest of the SL3 line.", "inherited"),
    _c("peacock_sl3034", "Peacock (Dandong)", "SL3034", 28800, 51.5, "etachron", True, (260, 310),
       "Day-date SL3 variant. One of the very few Chinese calibers with a published "
       "lift angle.", "documented"),
    _c("peacock_sl4801", "Peacock (Dandong)", "SL4801 / SL48", 28800, 53, "etachron", True,
       (250, 300), "41-jewel automatic chronograph, loosely Rolex 4130-adjacent. Published "
       "lift angle of 53. Expect a further 20-40 deg amplitude drop with the chrono running.",
       "documented"),
    _c("peacock_sl4609", "Peacock (Dandong)", "SL4609", 28800, 49, "etachron", False, (250, 300),
       "ETA 7750 based chronograph.", "inherited"),

    # ---- H.K. Precision Technology ----------------------------------------
    _c("pt5000", "HKPT", "PT5000", 28800, 52, "etachron", True, (270, 320),
       "ETA 2824-2 clone, designed in Hong Kong and built in Shenzhen. Lift angle of 52 "
       "comes from published bench measurements on real samples, and amplitude of 276-320 "
       "was recorded on those units -- so hold this one to a genuinely Swiss standard.",
       "measured"),
    _c("pt5100", "HKPT", "PT5100", 28800, 52, "etachron", False, (260, 310),
       "Hand-wind sibling of the PT5000.", "inherited"),

    # ---- Hangzhou ----------------------------------------------------------
    _c("hangzhou_6460", "Hangzhou", "6460 / 6460B", 28800, 52, "index", False, (240, 290),
       "Based on the HZ6300. Lift angle is formally unconfirmed.", "community"),
    _c("hangzhou_5000a", "Hangzhou", "5000A", 28800, 52, "index", False, (240, 290),
       "34-jewel micro-rotor automatic, only 3.95mm thick. Micro-rotors wind less "
       "efficiently, so low amplitude here is often a winding issue rather than an "
       "escapement one.", "community"),
    _c("hangzhou_3601", "Hangzhou", "3601", 28800, 50, "etachron", False, (245, 295),
       "Hangzhou's ETA 2824-2 clone.", "inherited"),

    # ---- PTS Resources / Dixmont Guangzhou --------------------------------
    _c("dg2813", "PTS / Dixmont", "DG2813 / DG-2813", 21600, 49, "index", False, (200, 260),
       "Miyota 8215 clone, 22 jewels. The budget end of the mod world. Amplitude in the "
       "220s is normal and not a fault. No moveable stud, so beat error is effectively "
       "fixed unless you turn the collet.", "inherited"),
    _c("dg3804", "PTS / Dixmont", "DG3804 / DG-3804", 21600, 49, "index", False, (200, 260),
       "DG2813 family with day-date. Often sold as an open-heart movement.", "inherited"),
    _c("dg4813", "PTS / Dixmont", "DG4813", 21600, 49, "index", False, (200, 260),
       "8215-derived, skeleton/open-heart variants common.", "inherited"),

    _c("st1612", "Sea-Gull", "ST1612 / ST16 series", 21600, 50, "index", False, (230, 285),
       "Thin automatic, loosely 2892-derived.", "inherited"),
    _c("st1701", "Sea-Gull", "ST1701 / ST17", 21600, 50, "index", False, (230, 285),
       "Hand-wind, 2892-adjacent geometry.", "inherited"),
    _c("st5601", "Sea-Gull", "ST5 / ST5601", 21600, 45, "index", False, (220, 280),
       "Small hand-wind, often in dress and skeleton pieces.", "inherited"),
    _c("st6", "Sea-Gull", "ST6 / TY2809", 21600, 50, "etachron", False, (240, 290),
       "2824-derived at 3 Hz rather than 4.", "inherited"),
    _c("st8000", "Sea-Gull", "ST8000 tourbillon", 21600, 50, "index", False, (200, 260),
       "Flying tourbillon. A rotating carriage changes the acoustic signature -- expect to "
       "tune the sub-noise threshold and treat amplitude with suspicion.", "inherited"),
    _c("peacock_sl1588", "Peacock (Dandong)", "SL1588", 21600, 51.5, "index", False, (230, 285),
       "Small automatic.", "inherited"),
    _c("peacock_sl5353", "Peacock (Dandong)", "SL5353 tourbillon", 21600, 51.5, "index", False,
       (200, 260), "Hand-wind tourbillon.", "inherited"),
    _c("peacock_d206", "Peacock (Dandong)", "D206 / SL30 base", 28800, 51.5, "etachron", False,
       (255, 305), "2824-derived.", "inherited"),
    _c("shanghai_7120", "Shanghai", "7120", 21600, 50, "index", False, (220, 275),
       "Shanghai Watch Factory automatic.", "inherited"),
    _c("shanghai_3120", "Shanghai", "3120 / A581 lineage", 21600, 50, "index", False, (220, 275),
       "Historic Chinese Standard Movement family.", "inherited"),
    _c("tianjin_ty2706", "Tianjin", "TY2706 / TY28 series", 28800, 50, "etachron", False,
       (245, 295), "Sea-Gull factory designation for 2824-derived movements sold OEM.",
       "inherited"),
    _c("sunon_pe80", "Sunon", "PE80 / SN36", 21600, 44, "index", False, (230, 285),
       "6497-style hand-wind. Budget alternative to the Sea-Gull ST3600.", "inherited"),
    _c("beijing_sb18", "Beijing", "SB18 / TB18", 28800, 50, "index", False, (240, 290),
       "Beijing Watch Factory. Higher-end Chinese in-house work.", "inherited"),
    _c("dg2813_skeleton", "PTS / Dixmont", "DG3833 / DG5833", 21600, 49, "index", False,
       (200, 260), "8215-derived skeleton variants.", "inherited"),
    _c("hangzhou_6008", "Hangzhou", "6008", 28800, 52, "index", False, (240, 290),
       "Lift angle unconfirmed.", "community"),

    _c("rolex_1030", "Rolex", "1030", 18000, 52, "index", True, (240, 300),
       "Rolex's first fully in-house automatic. Butterfly rotor.", "watchguy"),
    _c("rolex_1520", "Rolex", "1520 / 1530", 18000, 52, "index", True, (245, 300),
       "1500 family. The 1530 is the chronometer-grade version with a Microstella "
       "free-sprung balance; the 1520 is the economy variant with a regulator index "
       "and fewer jewels. Check which one you have before reaching for a tool.",
       "watchguy"),
    _c("rolex_3000", "Rolex", "3000", 28800, 52, "freesprung", False, (265, 310),
       "Modern 4 Hz base without the date quickset of the 3035. Microstella.",
       "inherited"),
    _c("rolex_3131", "Rolex", "3131", 28800, 52, "freesprung", False, (265, 310),
       "3130 with Parachrom hairspring inside a soft-iron antimagnetic cage. Used in "
       "the Milgauss and Air-King 116900.", "inherited"),
    _c("rolex_3230", "Rolex", "3230", 28800, 52, "freesprung", True, (270, 315),
       "Chronergy escapement, 70h reserve. Time-only sibling of the 3235.", "inherited"),
    _c("rolex_3155", "Rolex", "3155", 28800, 52, "freesprung", True, (265, 310),
       "Day-Date base. Microstella free-sprung.", "watchguy"),
    _c("rolex_3255", "Rolex", "3255 / 3256", 28800, 52, "freesprung", True, (270, 315),
       "Day-Date 40 base. Chronergy escapement, 70h, variable-inertia balance -- the "
       "3235 with a day disc.", "inherited"),

    # ---- Broader coverage (added 0.6.0) ---------------------------------
    # Swiss ebauches and in-house calibers that a lot of catalogued watches
    # actually use. Lift angles are manufacturer figures where one exists,
    # otherwise the WatchGuy value for the same or the parent caliber.
    _c("sw210_1", "Sellita", "SW210-1", 28800, 53, "etachron", True, (260, 310),
       "Hand-wind, ETA 2801-2 equivalent. Base of many Fliegers and field watches."),
    _c("sw221_1", "Sellita", "SW221-1", 28800, 50, "etachron", True, (260, 310),
       "SW200-1 with day/date, 2836-2 equivalent."),
    _c("sw290_1", "Sellita", "SW290-1", 28800, 50, "etachron", False, (240, 295),
       "Small automatic, 11.5 lignes."),
    _c("sw330_2", "Sellita", "SW330-2", 28800, 52, "etachron", True, (250, 300),
       "GMT / second time zone on an SW300 base, 56h reserve."),
    _c("soprod_a10", "Soprod", "A10 / M100", 28800, 49, "etachron", True, (250, 305),
       "ETA 2892-A2 form factor. Common in independents and mid-tier Swiss brands. "
       "Soprod lists 47 for the A10 and confirmed 49 for the current M100.", "documented"),
    _c("stp_1_11", "STP", "STP 1-11", 28800, 52, "etachron", True, (240, 310),
       "Fossil Group's 2824-2 clone (Swiss Technology Production). Published lift "
       "angle 52, acceptable amplitude 200-320. Used by Zodiac and some Fossil/"
       "Michele automatics.", "documented"),
    _c("ljp_g100", "La Joux-Perret", "G100 / G101", 28800, 51, "etachron", True, (250, 305),
       "68h automatic, SW200 form factor. Base for many 2020s microbrand and "
       "Swiss-mid pieces (Formex, Circula, Christopher Ward Bel Canto).", "documented"),
    _c("baumatic_bm13", "Baume & Mercier", "Baumatic BM12 / BM13", 28800, 52, "index", False,
       (250, 300), "120h reserve, silicon balance spring, antimagnetic. Richemont/ValFleurier."),
    _c("fc_303", "Frederique Constant", "FC-303 / FC-710 (Sellita base)", 28800, 50, "etachron",
       False, (250, 300)),
    _c("iwc_32110", "IWC", "32110 / 32111 / 32115", 28800, 52, "index", True, (255, 305),
       "72h in-house automatic (ValFleurier-derived) in the 2020s Mark XX, Pilot 41, "
       "Ingenieur 40."),
    _c("breitling_b01", "Breitling", "B01 / B02", 28800, 50, "index", False, (255, 305),
       "In-house column-wheel chronograph, 70h. Breitling does not publish a lift "
       "angle; 50 is the typical value for a Swiss column-wheel chrono. Expect the "
       "usual amplitude drop with the chrono running.", "community"),
    _c("zenith_elite", "Zenith", "Elite 670 / 679 / 6150", 28800, 52, "index", False, (255, 305),
       "Thin automatic time-only family. The El Primero (zenith_400) is the separate "
       "36,000 bph chronograph."),
    _c("panerai_p9000", "Panerai", "P.9000 / P.9010 / P.9100", 28800, 52, "index", False,
       (250, 300), "3-day automatic in-house family. P.9100 adds a flyback chrono."),
    _c("valjoux_7734", "ETA/Valjoux", "Valjoux 7734 / 7733 / 7736", 18000, 48, "index", True,
       (240, 290), "Vintage cam-lever hand-wind chronograph, 1960s-70s. Lift angle 48 "
       "(Caliber Corner).", "documented"),
    _c("valjoux_72", "ETA/Valjoux", "Valjoux 72 / 726", 18000, 50, "index", False, (240, 290),
       "Column-wheel hand-wind chronograph -- pre-Zenith Daytona, vintage Carrera, "
       "Autavia. Lift angle not published; 50 by analogy with the 773x family."),
    _c("eta_2472", "ETA", "2472 / 2452 / 2451", 18000, 52, "index", True, (240, 300),
       "1960s full-rotor automatic family that followed the bumper calibers."),
    _c("eta_2846", "ETA", "2846 / 2472 unidirectional", 18000, 52, "index", True, (240, 295)),

    # ---- Japanese, broader ---------------------------------------------
    _c("seiko_6119", "Seiko", "6119 / 6106 / 6118", 21600, 52, "index", False, (230, 285),
       "Late-1960s / 1970s automatics: 5 Sports, Bell-Matic-adjacent, 61xx family."),
    _c("seiko_6309", "Seiko", "6309 / 6306", 21600, 53, "index", False, (230, 285),
       "6309-7040 / -729x turtle diver. No hand-wind, no hacking, like the 7S26."),
    _c("seiko_7009", "Seiko", "7009 / 7019 / 7S25", 21600, 52, "index", False, (220, 280)),
    _c("seiko_4s15", "Seiko", "4S15 / 4S12 / 4S25", 28800, 52, "index_stud", False, (240, 290),
       "1990s mechanical revival (SCVS, early SARB), made by Seiko Instruments."),
    _c("seiko_6l35", "Seiko", "6L35 / 6L37", 28800, 52, "index_stud", False, (240, 290),
       "Thin 45h automatic by Seiko Instruments. King Seiko SPB, some Presage and "
       "grey-market Swiss-brand use."),
    _c("seiko_8l35", "Seiko", "8L35 / 8L55 / 8L45", 28800, 53, "index_stud", False, (260, 305),
       "Undecorated Grand Seiko 9S-family movement. Marinemaster 300 (SBDX), "
       "some Prospex LX and SLA divers. Commonly timed at 53 (some watchmakers "
       "use 52).", "community"),
    _c("seiko_9sa5", "Grand Seiko", "9SA5", 36000, 52, "freesprung", False, (250, 300),
       "Dual-impulse escapement, 80h, 36,000 bph. High-beat -- 250-280 is healthy."),
    _c("miyota_9110", "Miyota", "9110 / 9120 / 9122 / 9132", 28800, 51, "index", True, (245, 295),
       "9015 base with power-reserve (9110), GMT (9075 sits nearby), or small-second "
       "complications."),
    _c("citizen_0950", "Citizen", "Miyota 0950 / Cal. 0950 (The Citizen)", 28800, 51, "index",
       False, (245, 295), "Miyota's premium thin automatic; also seen as Cal. 9000-series."),

    # ---- Generic fallbacks ------------------------------------------------
    _c("generic_28800", "Generic", "Modern Swiss 4 Hz", 28800, 52, "index", False, (260, 310),
       "Fallback. Confirm the real lift angle before trusting amplitude."),
    _c("generic_21600", "Generic", "Modern 3 Hz", 21600, 52, "index", False, (240, 295),
       "Fallback. Confirm the real lift angle before trusting amplitude."),
    _c("generic_18000", "Generic", "Vintage 2.5 Hz", 18000, 44, "index", False, (230, 290),
       "Fallback. Vintage lift angles vary widely (38-60). Confirm before trusting amplitude."),
]

CALIBERS = {c.key: c for c in _LIST}

# "What's normal" detail for the common calibers -- power reserve (h), jewels,
# service interval (years), and the failure points worth knowing, ";"-separated.
_NORMS = {
    "eta_2824_2": (38, 25, 5,
        "Reversing wheels get gummy -- clean, treat with epilame, do not oil the pawls; "
        "date jumper spring launches easily; the etachron boot left too wide opens up "
        "the positional delta"),
    "eta_2836_2": (38, 25, 5, "As the 2824-2, plus a day-star that can bind if the "
        "quickset is forced against the changeover"),
    "eta_2892a2": (42, 21, 5, "Thin caliber with a narrow torque margin; the "
        "centre-seconds friction spring is fragile; a set mainspring shows as low "
        "amplitude that recovers after service"),
    "eta_7750": (44, 25, 5, "Amplitude drops 20-40 deg with the chrono running -- "
        "that is normal; the minute-counter jumper and the hammer cam faces need "
        "the right grease, not oil; check the hammer heart-piece contact"),
    "eta_6497_1": (46, 17, 6, "Long thin mainspring takes a set; big slow balance "
        "means amplitude in the 260s is fine; check the sub-seconds pinion"),
    "sw200_1": (38, 26, 5, "As the ETA 2824-2; some batches have had escape-wheel "
        "and pallet quality complaints -- inspect the stones"),
    "sw300_1": (56, 25, 5, "Sellita rates this as low as 200 deg amplitude at full "
        "wind -- do not chase 300; 56 h reserve from a longer mainspring"),
    "seiko_nh35": (41, 24, 6, "Magic-lever automatic -- do not oil the pawl levers; "
        "Diashock settings on balance and escape; parts are cheap enough that a new "
        "mainspring and pallet fork often beat fettling"),
    "seiko_7s26": (41, 21, 6, "No hand-wind, no hacking; factory beat error of "
        "0.5-1.0 ms is normal and not adjustable without moving the collet"),
    "seiko_6r15": (50, 23, 6, "Longer reserve than the NH35 from a Spron mainspring; "
        "otherwise the same automatic works and cautions"),
}
for _k, (_pr, _j, _si, _ki) in _NORMS.items():
    if _k in CALIBERS:
        CALIBERS[_k].power_reserve_h = _pr
        CALIBERS[_k].jewels = _j
        CALIBERS[_k].service_interval_years = _si
        CALIBERS[_k].known_issues = _ki

# Rated power reserve (hours) for calibers not covered above -- used only to
# sanity-check the reserve-run forecast, so an approximate manufacturer figure
# is fine.
_RESERVE_H = {
    "rolex_3000": 48, "rolex_3130": 48, "rolex_3131": 48, "rolex_3135": 50,
    "rolex_3155": 50, "rolex_3186": 50, "rolex_4130": 72,
    "rolex_3230": 70, "rolex_3235": 70, "rolex_3255": 70, "rolex_2235": 55,
    "rolex_1570": 42, "rolex_1520": 42, "rolex_1030": 42,
    "tudor_mt5602": 70, "tudor_2824": 38,
    "omega_2500": 48, "omega_8500": 60, "omega_8800": 55, "omega_8900": 60,
    "omega_1861": 48, "omega_321": 55, "omega_565": 50, "omega_1120": 44,
    "eta_2846": 40, "eta_2893_2": 42, "eta_2894_2": 42, "eta_2836_2": 38,
    "sw210_1": 42, "sw221_1": 38, "sw240": 38, "sw290_1": 42, "sw500": 62,
    "jlc_889": 43, "jlc_899": 43, "jlc_938": 43, "jlc_920": 45,
    "iwc_30110": 42, "iwc_32110": 72, "iwc_52010": 168, "iwc_79350": 44,
    "breitling_b01": 70, "miyota_9015": 42, "miyota_90s5": 42, "miyota_9110": 42,
    "panerai_p3000": 72, "panerai_p9000": 72,
    "eta_6497_2": 46, "seiko_6r35": 70, "seiko_4r35": 41, "seiko_nh36": 41,
    "eta_7753": 44,
}
for _k, _h in _RESERVE_H.items():
    if _k in CALIBERS and not CALIBERS[_k].power_reserve_h:
        CALIBERS[_k].power_reserve_h = float(_h)


# --------------------------------------------------------------------------
# Cross-reference: base movements and their clones / equivalents
# --------------------------------------------------------------------------
# `members` are (caliber_key_or_None, label). A key links to a CALIBERS entry;
# None is just a name for something not in the database.

CROSS_REF = [
    {"family": "ETA 2824-2", "base": "eta_2824_2",
     "note": "Same escapement geometry and largely interchangeable train and "
             "keyless parts; balance completes and mainsprings are the usual "
             "shared spares. Regulator hardware differs on some clones.",
     "members": [("eta_2824_2", "ETA 2824-2"), ("eta_2836_2", "ETA 2836-2 (day/date)"),
                 ("sw200_1", "Sellita SW200-1"), (None, "Sea-Gull ST2130"),
                 (None, "STP1-11 (Fossil/Zodiac)"), (None, "Hangzhou 6300"),
                 (None, "Myota 82S0 is NOT this -- different base")]},
    {"family": "ETA 2892-A2", "base": "eta_2892a2",
     "note": "Thinner base than the 2824, lower torque margin. The 2893 GMT and "
             "many chronograph modules sit on this.",
     "members": [("eta_2892a2", "ETA 2892-A2"), ("eta_2893_2", "ETA 2893-2 (GMT)"),
                 ("sw300_1", "Sellita SW300-1"), (None, "Sea-Gull ST18")]},
    {"family": "Valjoux 7750", "base": "eta_7750",
     "note": "Cam-switched chronograph. Expect a 20-40 deg amplitude drop with "
             "the chrono running on all of these.",
     "members": [("eta_7750", "ETA/Valjoux 7750"), ("sw500", "Sellita SW500"),
                 (None, "Sea-Gull ST19 / ST1901 shares the column-wheel lineage, "
                        "not the cam"), (None, "Concepto/La Joux-Perret 8147")]},
    {"family": "Unitas 6497 / 6498", "base": "eta_6497_1",
     "note": "Large slow pocket-watch caliber. 6497 has sub-seconds at 9, 6498 at 6.",
     "members": [("eta_6497_1", "ETA/Unitas 6497-1"), ("eta_6497_2", "ETA/Unitas 6497-2"),
                 (None, "Sea-Gull ST36 / ST3600 (6497 clone)"),
                 (None, "Sea-Gull TY2807 (6498 clone)")]},
    {"family": "Seiko NH35 / 4R35", "base": "seiko_nh35",
     "note": "Seiko Instruments (SII/TMI) automatic. Diashock, magic-lever "
             "winding, moveable stud arm so beat error is adjustable. Cheap parts.",
     "members": [("seiko_nh35", "Seiko NH35 / NH36"), (None, "Seiko 4R35 / 4R36 (same)"),
                 ("seiko_6r15", "Seiko 6R15 / 6R35 (related, longer reserve)")]},
    {"family": "Seiko 7S26", "base": "seiko_7s26",
     "note": "No hand-wind, no hacking, beat error not adjustable without moving "
             "the collet. Superseded by the NH/4R line.",
     "members": [("seiko_7s26", "Seiko 7S26 / 7S36"), (None, "Seiko 7S25 (no day)")]},
    {"family": "Miyota 9015", "base": None,
     "note": "Thin high-beat Miyota automatic. Known for a slightly audible "
             "rotor. The clone family copies the base plate closely.",
     "members": [(None, "Miyota 9015 / 90S5"), (None, "PT5000 (Perfect/Techma)"),
                 (None, "Landeron 24"), (None, "Sea-Gull ST1901 is unrelated")]},
]

_XREF_BY_KEY = {}
for _f in CROSS_REF:
    for _k, _ in _f["members"]:
        if _k:
            _XREF_BY_KEY[_k] = _f


def equivalents(key: str):
    """The cross-reference family for a caliber key, or None."""
    return _XREF_BY_KEY.get(key)


def whats_normal(cal) -> str:
    """A plain-language 'what to expect from a healthy one' summary."""
    lo, hi = cal.amp_full_wind if cal.amp_full_wind else (250.0, 315.0)
    lines = [f"# {cal.label}", ""]
    freq = f"{cal.bph:,} bph ({cal.bph / 7200:.1f} Hz)" if cal.bph else "auto-detected"
    lines.append(f"- **Beat rate**: {freq}")
    _srcmap = {"documented": "from documentation", "measured": "bench-measured",
               "community": "community value", "inherited": "inherited from the base",
               "watchguy": "WatchGuy list"}
    _src = _srcmap.get(getattr(cal, "lift_source", "community"), "")
    lines.append(f"- **Lift angle**: {cal.lift_angle:g} deg ({_src})")
    if cal.jewels:
        lines.append(f"- **Jewels**: {cal.jewels}")
    if cal.power_reserve_h:
        lines.append(f"- **Power reserve**: about {cal.power_reserve_h:g} h")
    lines.append(f"- **Amplitude, dial up, full wind**: {lo:.0f}-{hi:.0f} deg after a "
                 f"service; a few degrees lower is acceptable with age")
    lines.append("- **Amplitude, vertical positions**: 20-50 deg below the horizontal "
                 "figure is normal; more than 60 points at the hairspring or poise")
    lines.append("- **Beat error**: under 0.3 ms is the target; under 0.5 ms is fine")
    lines.append("- **Positional delta**: under 15 s/day is good for a modern caliber, "
                 "under 25 acceptable; vintage and budget movements run wider")
    lines.append(f"- **Service interval**: roughly every {cal.service_interval_years} years, "
                 f"or when amplitude or the positional delta has visibly drifted")
    reg = {
        "etachron": "Etachron: rate at the index, beat by rotating the stud carrier.",
        "index": "Plain index arm for rate; beat error needs the hairspring collet.",
        "index_stud": "Index arm for rate, moveable stud arm for beat.",
        "freesprung": "Free-sprung: rate by the balance inertia weights, beat by the "
                      "moveable stud carrier. No index.",
        "swan_neck": "Index with a swan-neck fine adjuster for rate.",
    }.get(cal.regulator, cal.regulator)
    lines.append(f"- **Regulating hardware**: {reg}")
    if cal.known_issues:
        lines.append("")
        lines.append("## Known weak points")
        for part in cal.known_issues.split(";"):
            part = part.strip()
            if part:
                lines.append(f"- {part}")
    if cal.notes:
        lines.append("")
        lines.append(f"_{cal.notes}_")
    fam = equivalents(cal.key)
    if fam:
        lines.append("")
        lines.append(f"**Equivalents**: " + "; ".join(
            lbl for k, lbl in fam["members"] if k != cal.key))
    return "\n".join(lines)


def _load_reference():
    """
    Merge the bulk WatchGuy lift-angle list.

    These carry no beat rate, so they are added with bph=None and the analyzer
    auto-detects. Curated entries always win a key collision -- they have beat
    rates, regulator types and amplitude expectations that the bulk list does
    not.
    """
    try:
        from .liftdata import parse
    except ImportError:
        return 0
    n = 0
    for brand, name, lift in parse():
        key = f"ref_{brand}_{name}".lower()
        key = "".join(ch if ch.isalnum() else "_" for ch in key)
        if key in CALIBERS:
            continue
        CALIBERS[key] = Caliber(
            key=key, brand=brand, name=name, bph=None, lift_angle=lift,
            regulator="index", verified=True, lift_source="watchguy",
            group="Reference list (WatchGuy)", escape_teeth=15,
            amp_full_wind=(230.0, 300.0),
            notes="From the WatchGuy lift-angle list. No beat rate published, so beat "
                  "rate is auto-detected. No regulator type recorded -- check the balance "
                  "cock yourself.")
        n += 1
    return n


REFERENCE_COUNT = _load_reference()

GROUP_ORDER = [
    "Swiss / European",
    "Japanese",
    "Chinese - clones",
    "Chinese - in-house",
    "Russian / Eastern Bloc",
    "Generic fallbacks",
    "My calibers",
    "Reference list (WatchGuy)",
]


def grouped():
    """Return {group: [Caliber, ...]} in display order."""
    out = {g: [] for g in GROUP_ORDER}
    for c in CALIBERS.values():
        out.setdefault(c.group, []).append(c)
    for v in out.values():
        v.sort(key=lambda c: (c.brand, c.name))
    return {g: v for g, v in out.items() if v}

REGULATOR_LABELS = {
    "etachron": "Etachron (index arm + rotating stud carrier and regulator boot)",
    "index": "Plain index/regulator arm (no moveable stud)",
    "index_stud": "Index arm + moveable stud arm",
    "freesprung": "Free-sprung (inertia weights, no index)",
    "swan_neck": "Index arm with swan-neck fine adjuster",
}


def search(query: str):
    """Fuzzy-ish lookup across brand, name and key."""
    def norm(t):
        # Strip every non-alphanumeric character. Brand names in the reference
        # list carry brackets and dots -- "A. Schild [AS]" -- and a search for
        # "as1686" has to survive them.
        return "".join(ch for ch in t.lower() if ch.isalnum())

    q = norm(query)
    if not q:
        return list(CALIBERS.values())
    out = []
    for c in CALIBERS.values():
        # Include the notes so a search for "2824", "dandong" or "8215" also
        # surfaces the clones that reference those in their description.
        if q in norm(c.key + c.brand + c.name + c.notes):
            out.append(c)
    # Shortest name first: an exact caliber number should outrank an entry
    # that merely mentions it in passing.
    out.sort(key=lambda c: (0 if q in norm(c.name) else 1, len(c.name), c.brand))
    return out


def load_user_calibers(path: str) -> int:
    """
    Merge a user CSV into the database. Columns (header required):
        key,brand,name,bph,lift_angle,regulator,amp_min,amp_max,notes

    Only key, brand, name, bph and lift_angle are required. This is how you
    pull in the full WatchGuy lift-angle list or shop-specific values.
    Returns the number of entries loaded.
    """
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                key = (row.get("key") or f"{row['brand']}_{row['name']}").strip()
                key = "".join(ch if ch.isalnum() else "_" for ch in key.lower())
                amin = float(row.get("amp_min") or 250)
                amax = float(row.get("amp_max") or 315)
                CALIBERS[key] = Caliber(
                    key=key,
                    brand=row["brand"].strip(),
                    name=row["name"].strip(),
                    bph=int(float(row["bph"])),
                    lift_angle=float(row["lift_angle"]),
                    regulator=(row.get("regulator") or "index").strip() or "index",
                    verified=str(row.get("verified", "")).strip().lower() in ("1", "true", "yes"),
                    lift_source=(row.get("lift_source") or "community").strip(),
                    group=(row.get("group") or "My calibers").strip(),
                    escape_teeth=int(float(row.get("escape_teeth") or 15)),
                    amp_full_wind=(amin, amax),
                    notes=(row.get("notes") or "").strip(),
                )
                n += 1
            except (KeyError, ValueError):
                continue
    return n


def snap_bph(measured_bph: float, tol_frac: float = 0.02) -> Optional[int]:
    """Snap a measured beat rate to the nearest standard value, if close enough."""
    best, best_err = None, 1e9
    for std in STANDARD_BPH:
        err = abs(measured_bph - std) / std
        if err < best_err:
            best, best_err = std, err
    return best if best_err <= tol_frac else None
