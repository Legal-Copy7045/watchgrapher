"""
Schematic watch illustrations.

There are no photographs in the catalogue -- manufacturer press images are
copyrighted and cannot ship with the app. Instead every model gets a clean
front-facing line drawing, generated from the fields the reference table
already carries: case metal, bezel style, crystal, nickname and the movement.

The output is a self-contained SVG string on a white background, sized to a
square. It is deliberately diagrammatic, not photoreal -- enough to tell a
fluted-bezel Datejust from a dive Submariner from a Royal Oak at a glance, and
to give a watch with no user photo something better than an empty grey box.

Public API:
    watch_svg(brand, model, reference="", material="", bezel="", crystal="",
              nickname="", notes="", caliber_key="", size=400)  -> str
    watch_svg_for(entry, size=400)     # entry: any object with those attrs
"""

from __future__ import annotations

import math

# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------

_METALS = {
    "steel":    dict(face="#dfe3e7", ring="#c2c8cf", edge="#8b939c", lug="#cdd3da"),
    "white":    dict(face="#e4e7ea", ring="#cfd4d9", edge="#98a0a9", lug="#d6dade"),
    "titanium": dict(face="#cfd3d4", ring="#b4b9bb", edge="#868b8d", lug="#c2c6c8"),
    "yellow":   dict(face="#eBcB77", ring="#d8b25c", edge="#a9822f", lug="#e4c06a"),
    "rose":     dict(face="#e7bda3", ring="#d7a184", edge="#a9714f", lug="#e0b096"),
    "bronze":   dict(face="#c69a5f", ring="#b0854b", edge="#7d5b30", lug="#bd8f55"),
    "dark":     dict(face="#33363b", ring="#26282c", edge="#141518", lug="#2c2e33"),
}


def _metal(material: str) -> dict:
    m = (material or "").lower()
    metallic = any(k in m for k in ("steel", "gold", "titan", "platinum",
                                    "bronze", "rolesor", "rolesium"))
    if not metallic and any(k in m for k in ("dlc", "pvd", "ceramic", "carbon",
                                             "black", "forged")):
        return _METALS["dark"]
    if "titan" in m:
        return _METALS["titanium"]
    if "bronze" in m:
        return _METALS["bronze"]
    if "everose" in m or "rose" in m or "pink" in m:
        return _METALS["rose"]
    if "yellow" in m or ("gold" in m and "white" not in m and "grey" not in m
                         and "everose" not in m):
        return _METALS["yellow"]
    if "platinum" in m or "rolesium" in m or "white gold" in m or "rhodium" in m:
        return _METALS["white"]
    # two-tone / rolesor: steel case, warm accents handled by the bezel colour
    return _METALS["steel"]


_COLOURS = {
    "black": "#1b1c1f", "blue": "#1f3f79", "navy": "#1b2f56", "green": "#1f5c3a",
    "hulk": "#1f7a41", "kermit": "#1f7a41", "olive": "#5c5f35",
    "brown": "#4a3527", "chocolate": "#3f2c20", "root beer": "#3f2c20",
    "white": "#f2f3f5", "polar": "#f2f3f5", "cream": "#efe7d5", "ivory": "#efe7d5",
    "silver": "#e6e8ea", "rhodium": "#d9dcdf", "grey": "#9a9ea4", "gray": "#9a9ea4",
    "slate": "#6f747b", "anthracite": "#3a3d42", "grey/slate": "#6f747b",
    "champagne": "#e4cf9a", "gold": "#e4cf9a", "salmon": "#e8b6a0",
    "meteorite": "#8f9296", "tiffany": "#79d0c1", "turquoise": "#3fb6c0",
    "pepsi": "#1b1c1f", "batman": "#1b1c1f", "coke": "#1b1c1f",
    "panda": "#f2f3f5", "reverse panda": "#1b1c1f", "tuxedo": "#f2f3f5",
}


def _find_colour(*texts) -> str:
    hay = " ".join(t for t in texts if t).lower()
    # multi-word keys first
    for key in ("root beer", "reverse panda", "grey/slate"):
        if key in hay:
            return _COLOURS[key]
    for key, val in _COLOURS.items():
        if key in hay.split() or key in hay:
            return val
    return ""


# --------------------------------------------------------------------------
# style inference
# --------------------------------------------------------------------------

def _shape(brand: str, model: str) -> str:
    b, m = (brand or "").lower(), (model or "").lower()
    t = f"{b} {m}"
    if "royal oak" in t:
        return "octagon"
    if "nautilus" in m or "overseas" in m or "aquanaut" in m or "laureato" in m:
        return "porthole"
    if "tank" in m or "santos" in m or "reverso" in m or "toledo" in m:
        return "rect"
    if "radiomir" in m or "luminor" in m or ("panerai" in b) or "cushion" in t \
            or "monaco" in m:
        return "cushion"
    if "tonneau" in m or "spirit of big bang" in m:
        return "tonneau"
    return "round"


def _bezel_kind(bezel: str, model: str, caliber_key: str) -> str:
    z, m, c = (bezel or "").lower(), (model or "").lower(), (caliber_key or "").lower()
    if "fluted" in z or "gadroon" in z:
        return "fluted"
    if "24h" in z or "24-h" in z or "gmt" in m or "worldtime" in m or "world time" in m:
        return "gmt"
    if "tachy" in z or "tachy" in m or "daytona" in m or "speedmaster" in m:
        return "tachy"
    if any(k in z for k in ("dive", "60", "ceramic", "aluminum", "aluminium",
                            "rotating", "unidirectional", "count")) \
            or any(k in m for k in ("submariner", "sea-dweller", "seamaster", "diver",
                                    "pelagos", "black bay", "aquaracer", "fifty fathoms",
                                    "turtle", "samurai", "monster", "skx", "deepsea",
                                    "planet ocean", "superocean")):
        return "dive"
    if "engine" in z or "engine-turned" in z or "knurled" in z:
        return "knurled"
    return "smooth"


def _has_date(model: str, caliber_key: str, notes: str, bezel: str) -> bool:
    t = f"{model} {caliber_key} {notes}".lower()
    if any(k in t for k in ("no date", "no-date", "sans date", "114060", "124060",
                            "14060", "5513", "114270", "no_date")):
        return False
    if any(k in t for k in ("date", "datejust", "day-date", "daydate", "dj", "gmt",
                            "2824", "2836", "3135", "3235", "3285", "7750", "nh35",
                            "eta_28", "6r15", "sw200", "explorer ii", "yacht", "sub")):
        return True
    return "day-date" in t or "daydate" in t


def _is_chrono(model: str, caliber_key: str) -> bool:
    t = f"{model} {caliber_key}".lower()
    return any(k in t for k in ("chrono", "daytona", "speedmaster", "7750", "4130",
                                "el primero", "1861", "3861", "b01", "valjoux",
                                "7734", "7733", "cal 11", "calibre 11"))


def _is_subsec(model: str, caliber_key: str) -> bool:
    t = f"{model} {caliber_key}".lower()
    return any(k in t for k in ("6497", "6498", "unitas", "small second", "sub-second",
                                "st36", "3600", "petite seconde"))


def _is_gmt(model: str, caliber_key: str, bezel: str) -> bool:
    t = f"{model} {caliber_key} {bezel}".lower()
    return "gmt" in t or "24h" in t or "worldtime" in t or "world time" in t


def _dial_colour(brand, model, bezel, nickname, notes, metal) -> str:
    explicit = _find_colour(nickname, notes, bezel)
    if explicit:
        return explicit
    t = f"{brand} {model}".lower()
    if any(k in t for k in ("submariner", "sea-dweller", "deepsea", "diver", "pelagos",
                            "black bay", "fifty fathoms", "seamaster", "aquaracer",
                            "planet ocean", "monster", "turtle", "skx", "luminor")):
        return _COLOURS["black"]
    if any(k in t for k in ("datejust", "day-date", "oyster perpetual", "calatrava",
                            "dress", "de ville", "saxonia", "tank", "master ultra thin",
                            "1815", "portugieser", "portofino")):
        return "#e9eaec"
    if metal is _METALS["yellow"] or metal is _METALS["rose"]:
        return _COLOURS["champagne"]
    return "#23262d"


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def _pol(cx, cy, r, deg):
    """0 deg = 12 o'clock, increasing clockwise."""
    a = math.radians(deg - 90.0)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def _p(x, y):
    return f"{x:.2f},{y:.2f}"


# --------------------------------------------------------------------------
# renderer
# --------------------------------------------------------------------------

def watch_svg(brand="", model="", reference="", material="", bezel="",
              crystal="", nickname="", notes="", caliber_key="", size=400) -> str:
    S = float(size)
    cx = cy = S / 2.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S:.0f} {S:.0f}" '
        f'width="{S:.0f}" height="{S:.0f}" role="img" '
        f'aria-label="{_esc(brand)} {_esc(model)} {_esc(reference)}">',
        f'<rect width="{S:.0f}" height="{S:.0f}" fill="#ffffff"/>',
    ]

    metal = _metal(material)
    shape = _shape(brand, model)
    bz_kind = _bezel_kind(bezel, model, caliber_key)
    bz_col = _find_colour(bezel, nickname, notes)
    dial = _dial_colour(brand, model, bezel, nickname, notes, metal)
    dark_dial = _luma(dial) < 0.45
    ink = "#f4f5f6" if dark_dial else "#1c1e22"
    faint = "#9aa0a8" if dark_dial else "#7b818a"
    date = _has_date(model, caliber_key, notes, bezel)
    chrono = _is_chrono(model, caliber_key)
    subsec = _is_subsec(model, caliber_key)
    gmt = _is_gmt(model, caliber_key, bezel)

    R_case = S * 0.40
    R_bez = S * 0.335
    R_dial = S * 0.30

    # ---- bracelet / strap stubs behind the case ----
    band = metal["lug"] if _is_bracelet(material, model) else _strap_colour(notes, dial)
    bw = S * 0.30
    parts.append(
        f'<path d="M {_p(cx-bw/2, 0)} L {_p(cx+bw/2, 0)} '
        f'L {_p(cx+bw*0.42, cy)} L {_p(cx-bw*0.42, cy)} Z" fill="{band}"/>')
    parts.append(
        f'<path d="M {_p(cx-bw*0.42, cy)} L {_p(cx+bw*0.42, cy)} '
        f'L {_p(cx+bw/2, S)} L {_p(cx-bw/2, S)} Z" fill="{band}"/>')
    if _is_bracelet(material, model):
        for gy in (S*0.16, S*0.30, S*0.70, S*0.84):
            parts.append(f'<line x1="{cx-bw*0.44:.1f}" y1="{gy:.1f}" '
                         f'x2="{cx+bw*0.44:.1f}" y2="{gy:.1f}" '
                         f'stroke="{metal["edge"]}" stroke-width="1" opacity="0.5"/>')

    # ---- case ----
    parts.append(_case_path(cx, cy, R_case, shape, metal["face"], metal["edge"]))

    # crown (+ guards)
    crx, cry = _pol(cx, cy, R_case, 90)
    if "crown guard" in (notes or "").lower() or bz_kind == "dive" or "submariner" in model.lower():
        parts.append(f'<path d="M {_p(crx-2, cry-S*0.05)} L {_p(crx+S*0.03, cry-S*0.03)} '
                     f'L {_p(crx+S*0.03, cry+S*0.03)} L {_p(crx-2, cry+S*0.05)} Z" '
                     f'fill="{metal["ring"]}" stroke="{metal["edge"]}" stroke-width="1"/>')
    parts.append(f'<rect x="{crx-1:.1f}" y="{cry-S*0.028:.1f}" width="{S*0.055:.1f}" '
                 f'height="{S*0.056:.1f}" rx="2" fill="{metal["ring"]}" '
                 f'stroke="{metal["edge"]}" stroke-width="1"/>')

    # ---- bezel ----
    parts.append(_bezel(cx, cy, R_bez, R_dial * 1.02, bz_kind, bz_col, metal, dark_dial))

    # ---- dial ----
    parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R_dial:.1f}" fill="{dial}" '
                 f'stroke="{metal["edge"]}" stroke-width="1"/>')

    # minute track
    for i in range(60):
        rr1 = R_dial * 0.94
        rr2 = R_dial * (0.90 if i % 5 else 0.86)
        x1, y1 = _pol(cx, cy, rr1, i * 6)
        x2, y2 = _pol(cx, cy, rr2, i * 6)
        parts.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                     f'stroke="{faint}" stroke-width="{1.6 if i % 5 == 0 else 0.7:.1f}"/>')

    # sub-dials
    sub_centres = []
    if chrono:
        for ang in (270, 90, 180):          # 9, 3, 6
            sx, sy = _pol(cx, cy, R_dial * 0.46, ang)
            sub_centres.append((sx, sy))
            parts.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{R_dial*0.20:.1f}" '
                         f'fill="none" stroke="{faint}" stroke-width="1.2"/>')
            parts.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="1.6" fill="{ink}"/>')
    elif subsec:
        sx, sy = _pol(cx, cy, R_dial * 0.5, 180)
        sub_centres.append((sx, sy))
        parts.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{R_dial*0.22:.1f}" '
                     f'fill="none" stroke="{faint}" stroke-width="1.2"/>')
        parts.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="1.6" fill="{ink}"/>')

    # hour markers
    for h in range(12):
        ang = h * 30
        if h == 0:
            # triangle at 12 for sport dials, baton otherwise
            if bz_kind in ("dive", "gmt") or chrono:
                t1 = _pol(cx, cy, R_dial * 0.82, 0)
                t2 = _pol(cx, cy, R_dial * 0.70, -7)
                t3 = _pol(cx, cy, R_dial * 0.70, 7)
                parts.append(f'<path d="M {_p(*t1)} L {_p(*t2)} L {_p(*t3)} Z" '
                             f'fill="{ink}"/>')
                continue
        if date and _date_at(model, caliber_key) == h:
            continue
        if any(_near(_pol(cx, cy, R_dial * 0.62, ang), sc, R_dial * 0.24)
               for sc in sub_centres):
            continue
        mx, my = _pol(cx, cy, R_dial * 0.72, ang)
        if h in (0, 3, 6, 9):
            parts.append(f'<circle cx="{mx:.2f}" cy="{my:.2f}" r="{R_dial*0.055:.1f}" '
                         f'fill="{ink}"/>')
        else:
            ex, ey = _pol(cx, cy, R_dial * 0.80, ang)
            parts.append(f'<line x1="{mx:.2f}" y1="{my:.2f}" x2="{ex:.2f}" y2="{ey:.2f}" '
                         f'stroke="{ink}" stroke-width="{R_dial*0.055:.1f}" '
                         f'stroke-linecap="round"/>')

    # date window (+ Rolex cyclops)
    if date:
        dh = _date_at(model, caliber_key)
        dwx, dwy = _pol(cx, cy, R_dial * 0.72, dh * 30)
        w = R_dial * 0.20
        parts.append(f'<rect x="{dwx-w/2:.1f}" y="{dwy-w*0.42:.1f}" width="{w:.1f}" '
                     f'height="{w*0.84:.1f}" fill="#ffffff" stroke="{faint}" '
                     f'stroke-width="1"/>')
        parts.append(f'<text x="{dwx:.1f}" y="{dwy+w*0.22:.1f}" text-anchor="middle" '
                     f'font-family="Segoe UI,Arial,sans-serif" font-size="{w*0.62:.1f}" '
                     f'fill="#1c1e22">31</text>')
        if "rolex" in (brand or "").lower() and (crystal or "").lower().startswith("sap"):
            parts.append(f'<circle cx="{dwx:.1f}" cy="{dwy:.1f}" r="{w*0.62:.1f}" '
                         f'fill="#ffffff" opacity="0.25" stroke="#c9ccd0" '
                         f'stroke-width="1"/>')

    # hands
    parts.append(_hand(cx, cy, R_dial * 0.30, R_dial * 0.44, 300, ink, 4.2))   # hour
    parts.append(_hand(cx, cy, R_dial * 0.30, R_dial * 0.66, 110, ink, 3.0))   # minute
    if gmt:
        parts.append(_gmt_hand(cx, cy, R_dial * 0.60, 40,
                               "#c94b3b" if not dark_dial else "#e2604f"))
    parts.append(_hand(cx, cy, R_dial * 0.16, R_dial * 0.66, 200,
                       "#c94b3b" if not (chrono or subsec) else ink, 1.1))     # seconds
    parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R_dial*0.045:.1f}" '
                 f'fill="{ink}"/>')

    # text: brand under 12, model/nickname over 6
    label_top = (brand or "").upper()
    label_bot = (nickname or model or "").upper()
    if label_top:
        parts.append(f'<text x="{cx:.1f}" y="{cy - R_dial*0.34:.1f}" text-anchor="middle" '
                     f'font-family="Georgia,\'Times New Roman\',serif" '
                     f'font-size="{R_dial*0.13:.1f}" letter-spacing="0.5" '
                     f'fill="{ink}">{_esc(label_top[:18])}</text>')
    if label_bot:
        parts.append(f'<text x="{cx:.1f}" y="{cy + R_dial*0.40:.1f}" text-anchor="middle" '
                     f'font-family="Georgia,\'Times New Roman\',serif" '
                     f'font-size="{R_dial*0.095:.1f}" letter-spacing="0.4" '
                     f'fill="{faint}">{_esc(label_bot[:22])}</text>')

    # crystal glare
    gx, gy = _pol(cx, cy, R_dial * 0.5, 315)
    parts.append(f'<ellipse cx="{gx:.1f}" cy="{gy:.1f}" rx="{R_dial*0.42:.1f}" '
                 f'ry="{R_dial*0.16:.1f}" fill="#ffffff" opacity="0.10" '
                 f'transform="rotate(-35 {gx:.1f} {gy:.1f})"/>')

    # reference number, small, bottom-right on the white
    if reference:
        parts.append(f'<text x="{S-8:.0f}" y="{S-8:.0f}" text-anchor="end" '
                     f'font-family="Segoe UI,Arial,sans-serif" font-size="{S*0.032:.1f}" '
                     f'fill="#b6bcc4">{_esc(reference)}</text>')

    parts.append("</svg>")
    return "".join(parts)


def watch_svg_for(entry, size=400) -> str:
    g = lambda *names: next((getattr(entry, n) for n in names
                             if getattr(entry, n, None)), "")
    return watch_svg(
        brand=g("brand"), model=g("model"), reference=g("reference"),
        material=g("material"), bezel=g("bezel"), crystal=g("crystal"),
        nickname=g("nickname"), notes=g("notes"),
        caliber_key=g("caliber_key"), size=size)


# --------------------------------------------------------------------------
# drawing helpers
# --------------------------------------------------------------------------

def _case_path(cx, cy, r, shape, fill, edge):
    sw = f'fill="{fill}" stroke="{edge}" stroke-width="2"'
    if shape == "round" or shape == "porthole":
        return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" {sw}/>'
    if shape == "rect":
        w, h = r * 1.5, r * 2.0
        return (f'<rect x="{cx-w/2:.1f}" y="{cy-h/2:.1f}" width="{w:.1f}" '
                f'height="{h:.1f}" rx="{r*0.16:.1f}" {sw}/>')
    if shape == "cushion":
        w = r * 1.9
        return (f'<rect x="{cx-w/2:.1f}" y="{cy-w/2:.1f}" width="{w:.1f}" '
                f'height="{w:.1f}" rx="{r*0.42:.1f}" {sw}/>')
    if shape == "tonneau":
        w, h = r * 1.7, r * 2.0
        return (f'<rect x="{cx-w/2:.1f}" y="{cy-h/2:.1f}" width="{w:.1f}" '
                f'height="{h:.1f}" rx="{r*0.55:.1f}" ry="{r*0.42:.1f}" {sw}/>')
    if shape == "octagon":
        pts = " ".join(_p(*_pol(cx, cy, r * 1.06, a))
                       for a in range(22, 360, 45))
        return f'<polygon points="{pts}" {sw}/>'
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" {sw}/>'


def _bezel(cx, cy, r_out, r_in, kind, colour, metal, dark_dial):
    out = []
    ring_fill = colour or metal["ring"]
    out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r_out:.1f}" fill="{ring_fill}" '
               f'stroke="{metal["edge"]}" stroke-width="1.5"/>')
    if kind == "fluted":
        for a in range(0, 360, 6):
            x1, y1 = _pol(cx, cy, r_in, a)
            x2, y2 = _pol(cx, cy, r_out, a)
            out.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                       f'stroke="{metal["edge"]}" stroke-width="1" opacity="0.55"/>')
    elif kind == "knurled":
        for a in range(0, 360, 9):
            x1, y1 = _pol(cx, cy, (r_in + r_out) / 2, a)
            out.append(f'<circle cx="{x1:.2f}" cy="{y1:.2f}" r="1.1" '
                       f'fill="{metal["edge"]}" opacity="0.5"/>')
    elif kind in ("dive", "gmt"):
        step = 15 if kind == "gmt" else 5
        rmid = (r_in + r_out) / 2
        for a in range(0, 360, step):
            x1, y1 = _pol(cx, cy, r_in * 1.02, a)
            x2, y2 = _pol(cx, cy, r_out * 0.96, a)
            out.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                       f'stroke="#e8eaec" stroke-width="{1.6 if a % 30 == 0 else 0.9:.1f}" '
                       f'opacity="0.85"/>')
        if kind == "gmt":
            for a in range(0, 360, 90):
                nx, ny = _pol(cx, cy, rmid, a)
                num = {0: "24", 90: "6", 180: "12", 270: "18"}[a]
                out.append(f'<text x="{nx:.1f}" y="{ny+3:.1f}" text-anchor="middle" '
                           f'font-family="Arial,sans-serif" font-size="{(r_out-r_in)*0.7:.1f}" '
                           f'fill="#e8eaec">{num}</text>')
        # pip at 12
        px, py = _pol(cx, cy, r_out * 0.92, 0)
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.4" fill="#e8eaec"/>')
    elif kind == "tachy":
        for a in range(0, 360, 6):
            x1, y1 = _pol(cx, cy, r_in, a)
            x2, y2 = _pol(cx, cy, r_out * 0.95, a)
            out.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                       f'stroke="{_ink_on(ring_fill)}" stroke-width="0.8" opacity="0.7"/>')
    # inner lip
    out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r_in:.1f}" fill="none" '
               f'stroke="{metal["edge"]}" stroke-width="1"/>')
    return "".join(out)


def _hand(cx, cy, back, length, deg, fill, width):
    tipx, tipy = _pol(cx, cy, length, deg)
    bx, by = _pol(cx, cy, back, deg + 180)
    lx, ly = _pol(cx, cy, length * 0.7, deg - 2.6)
    rx, ry = _pol(cx, cy, length * 0.7, deg + 2.6)
    return (f'<path d="M {_p(bx, by)} L {_p(lx, ly)} L {_p(tipx, tipy)} '
            f'L {_p(rx, ry)} Z" fill="{fill}" stroke="{fill}" '
            f'stroke-width="{width:.1f}" stroke-linejoin="round"/>')


def _gmt_hand(cx, cy, length, deg, fill):
    tipx, tipy = _pol(cx, cy, length, deg)
    a1 = _pol(cx, cy, length * 0.82, deg - 5)
    a2 = _pol(cx, cy, length * 0.82, deg + 5)
    bx, by = _pol(cx, cy, length * 0.30, deg + 180)
    return (f'<g stroke="{fill}" stroke-width="2.4" stroke-linecap="round">'
            f'<line x1="{bx:.1f}" y1="{by:.1f}" x2="{tipx:.1f}" y2="{tipy:.1f}"/></g>'
            f'<path d="M {_p(*a1)} L {_p(tipx, tipy)} L {_p(*a2)} Z" fill="{fill}"/>')


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _luma(hexcol):
    try:
        h = hexcol.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    except Exception:
        return 0.5


def _ink_on(hexcol):
    return "#f4f5f6" if _luma(hexcol) < 0.5 else "#1c1e22"


def _near(pt, centre, radius):
    return math.hypot(pt[0] - centre[0], pt[1] - centre[1]) < radius


def _date_at(model, caliber_key):
    t = f"{model} {caliber_key}".lower()
    if "7750" in t or "valjoux" in t or "b01" in t:
        return 3
    if any(k in t for k in ("6497", "6498", "unitas", "sub-second")):
        return 6
    return 3


def _is_bracelet(material, model):
    m = (material or "").lower()
    if any(k in m for k in ("steel", "gold", "rolesor", "rolesium", "titanium",
                            "platinum", "bronze", "oyster")):
        return True
    return any(k in (model or "").lower()
               for k in ("oyster", "jubilee", "president", "integrated"))


def _strap_colour(notes, dial):
    n = (notes or "").lower()
    if "rubber" in n or "tropic" in n:
        return "#26282c"
    if "leather" in n or "alligator" in n or "croc" in n:
        return "#3a2a1e"
    if "nato" in n or "fabric" in n or "textile" in n:
        return "#3d4450"
    return "#2f3138"
