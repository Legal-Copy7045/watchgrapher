"""
Service report.

Produces one self-contained HTML file with no external assets, so it survives
being emailed and opens on anything. Print to PDF from the browser if a PDF is
what is wanted -- that avoids adding a PDF library to the dependency list for
a feature used once per job.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import os
from datetime import datetime

import numpy as np

CSS = """
:root{--ink:#1b2028;--mut:#697687;--line:#d8dee7;--good:#1f8a4c;--warn:#b7791f;--bad:#c0392b}
*{box-sizing:border-box}
body{font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
     color:var(--ink);max-width:900px;margin:32px auto;padding:0 24px}
h1{font-size:22px;margin:0 0 2px}
h2{font-size:15px;margin:28px 0 8px;padding-bottom:5px;border-bottom:1px solid var(--line);
   text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}
.sub{color:var(--mut);margin:0 0 20px}
table{border-collapse:collapse;width:100%;margin:6px 0 4px}
th,td{padding:6px 9px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.cards{display:flex;gap:10px;margin:10px 0 4px;flex-wrap:wrap}
.card{flex:1 1 130px;border:1px solid var(--line);border-radius:7px;padding:10px 12px}
.card .k{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}
.card .v{font-size:25px;font-weight:600;font-variant-numeric:tabular-nums}
.f{margin:11px 0;padding-left:11px;border-left:3px solid var(--line)}
.f.critical{border-color:var(--bad)} .f.warn{border-color:var(--warn)}
.f.good{border-color:var(--good)} .f .t{font-weight:600}
.f.critical .t{color:var(--bad)} .f.warn .t{color:var(--warn)} .f.good .t{color:var(--good)}
.f .d{color:#3c4654;font-size:13px}
.foot{margin-top:34px;padding-top:10px;border-top:1px solid var(--line);
      color:var(--mut);font-size:11px}
@media print{body{margin:0;max-width:none}h2{page-break-after:avoid}.f{page-break-inside:avoid}}
"""


def _fmt(v, dp=1, dash="--"):
    try:
        if v != v:
            return dash
        return f"{v:.{dp}f}"
    except (TypeError, ValueError):
        return dash


def trace_svg(m, nominal_bph, width_ms=20.0, w=820, h=300):
    """Redraw the timegrapher trace as inline SVG so the report stands alone."""
    from .analysis import trace_points
    xt, yt, xk, yk = trace_points(m, nominal_bph, width_ms)
    if xt.size == 0 and xk.size == 0:
        return "<p style='color:#697687'>No trace captured.</p>"

    pad_l, pad_b, pad_t, pad_r = 46, 30, 10, 10
    ymax = max(float(yt.max()) if yt.size else 0.0,
               float(yk.max()) if yk.size else 0.0, 1e-6)
    half = width_ms / 2.0

    def sx(v):
        return pad_l + (v + half) / width_ms * (w - pad_l - pad_r)

    def sy(v):
        return pad_t + v / ymax * (h - pad_t - pad_b)

    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" '
             f'style="border:1px solid #d8dee7;border-radius:6px;background:#fff">']
    for gx in np.linspace(-half, half, 9):
        x = sx(gx)
        parts.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{h-pad_b}" '
                     f'stroke="#eef1f5"/>')
        parts.append(f'<text x="{x:.1f}" y="{h-pad_b+15}" font-size="10" fill="#697687" '
                     f'text-anchor="middle">{gx:.0f}</text>')
    for gy in np.linspace(0, ymax, 5):
        y = sy(gy)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w-pad_r}" y2="{y:.1f}" '
                     f'stroke="#eef1f5"/>')
        parts.append(f'<text x="{pad_l-6}" y="{y+3:.1f}" font-size="10" fill="#697687" '
                     f'text-anchor="end">{gy:.0f}s</text>')
    for xs, ys, col in ((xt, yt, "#1f6fd0"), (xk, yk, "#d97a1f")):
        for xv, yv in zip(xs, ys):
            parts.append(f'<circle cx="{sx(xv):.1f}" cy="{sy(yv):.1f}" r="1.7" fill="{col}"/>')
    parts.append(f'<text x="{w/2:.0f}" y="{h-4}" font-size="10" fill="#697687" '
                 f'text-anchor="middle">deviation (ms) -- slope is rate, '
                 f'gap between the two colours is beat error</text>')
    parts.append("</svg>")
    return "".join(parts)


def build(path, caliber, readings, measurement=None, findings=None,
          fault_report=None, tuning=None, reserve_log=None,
          watch_label="", technician="", notes=""):
    """Write the report and return its path."""
    e = html.escape
    now = datetime.now().strftime("%d %B %Y, %H:%M")
    cal_name = caliber.label if caliber else "Unspecified"
    bph = (f"{caliber.bph} bph" if caliber and caliber.bph else "auto-detected")

    p = [f"<!doctype html><html><head><meta charset='utf-8'>",
         f"<title>Timing report -- {e(cal_name)}</title><style>{CSS}</style></head><body>"]
    p.append(f"<h1>Timing report{' -- ' + e(watch_label) if watch_label else ''}</h1>")
    p.append(f"<p class='sub'>{e(cal_name)} &middot; {bph} &middot; lift angle "
             f"{caliber.lift_angle:g}&deg; &middot; {now}"
             f"{' &middot; ' + e(technician) if technician else ''}</p>")

    if measurement is not None and measurement.ok:
        m = measurement
        p.append("<h2>Latest reading</h2><div class='cards'>")
        for k, v in (("Rate", f"{m.rate:+.1f} s/d"),
                     ("Amplitude", f"{_fmt(m.amplitude, 0)}&deg;"),
                     ("Beat error", f"{_fmt(m.beat_error, 2)} ms"),
                     ("Beat rate", f"{m.detected_bph} bph")):
            p.append(f"<div class='card'><div class='k'>{k}</div><div class='v'>{v}</div></div>")
        p.append("</div>")
        p.append(f"<p class='sub' style='margin-top:6px'>{m.beats} beats, SNR "
                 f"{m.snr_db:.0f} dB, template match {m.quality:.2f}, "
                 f"{3 + m.extra_peaks:.1f} noises per beat.</p>")
        p.append(trace_svg(m, m.nominal_bph or m.detected_bph))

    if readings:
        p.append("<h2>Positional results</h2><table><tr><th>Position</th><th>Wind</th>"
                 "<th class='n'>Rate s/d</th><th class='n'>Amplitude</th>"
                 "<th class='n'>Beat error ms</th></tr>")
        for r in readings:
            p.append(f"<tr><td>{e(r.position)}</td><td>{e(r.wind_state)}</td>"
                     f"<td class='n'>{r.rate:+.1f}</td>"
                     f"<td class='n'>{_fmt(r.amplitude, 0)}</td>"
                     f"<td class='n'>{_fmt(r.beat_error, 2)}</td></tr>")
        rates = [r.rate for r in readings if r.rate == r.rate]
        amps = [r.amplitude for r in readings if r.amplitude == r.amplitude]
        if len(rates) >= 2:
            p.append(f"<tr><td colspan='2'><b>Delta / mean</b></td>"
                     f"<td class='n'><b>{max(rates)-min(rates):.1f} / "
                     f"{sum(rates)/len(rates):+.1f}</b></td>"
                     f"<td class='n'><b>{(f'{max(amps)-min(amps):.0f} drop' if len(amps)>=2 else '--')}"
                     f"</b></td><td></td></tr>")
        p.append("</table>")

    if reserve_log:
        p.append("<h2>Power reserve run</h2><table><tr><th class='n'>Hours</th>"
                 "<th class='n'>Rate s/d</th><th class='n'>Amplitude</th>"
                 "<th class='n'>Beat error ms</th></tr>")
        step = max(1, len(reserve_log) // 25)
        for row in reserve_log[::step]:
            p.append(f"<tr><td class='n'>{row[0]/3600.0:.2f}</td>"
                     f"<td class='n'>{row[1]:+.1f}</td>"
                     f"<td class='n'>{_fmt(row[2], 0)}</td>"
                     f"<td class='n'>{_fmt(row[3], 2)}</td></tr>")
        p.append("</table>")

    if findings:
        p.append("<h2>Assessment</h2>")
        for f in findings:
            p.append(f"<div class='f {e(f.severity)}'><div class='t'>{e(f.title)}</div>"
                     f"<div class='d'>{e(f.detail)}</div></div>")

    if fault_report is not None and fault_report.ok:
        p.append("<h2>Periodic fault scan</h2>")
        if fault_report.periods:
            p.append("<table><tr><th>Component</th><th class='n'>Swing ms</th>"
                     "<th class='n'>Period s</th><th class='n'>vs noise</th></tr>")
            for q in fault_report.periods:
                p.append(f"<tr><td>{e(q.component)}</td>"
                         f"<td class='n'>{q.amplitude_ms:.3f}</td>"
                         f"<td class='n'>{q.period_seconds:.2f}</td>"
                         f"<td class='n'>{q.snr:.0f}x</td></tr>")
            p.append("</table>")
            for q in fault_report.periods:
                p.append(f"<div class='f warn'><div class='t'>{e(q.component)}</div>"
                         f"<div class='d'>{e(q.detail)}</div></div>")
        else:
            p.append(f"<p class='sub'>{e(fault_report.message)}</p>")

    if notes:
        p.append(f"<h2>Notes</h2><p>{e(notes).replace(chr(10), '<br>')}</p>")

    if tuning:
        p.append(f"<h2>Measurement conditions</h2><p class='sub'>Filter "
                 f"{tuning.get('band_lo', 0):.0f}-{tuning.get('band_hi', 0):.0f} Hz, "
                 f"envelope window {tuning.get('env_win_ms', 0):.2f} ms, "
                 f"sub-noise threshold {tuning.get('sub_threshold', 0):.2f}.</p>")

    p.append("<div class='foot'>Measured acoustically with WatchGrapher. Amplitude "
             "accuracy is limited by how well the lift angle is known; rate and beat "
             "error are not affected by it. Bench figures are not wrist performance.</div>")
    p.append("</body></html>")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(p))
    return os.path.abspath(path)


# ==========================================================================
# Watch report -- profile plus the whole timing history
# ==========================================================================

def _embed_image(path):
    """Inline the photo as a data URI so the report is one portable file."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return None
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    return f"data:{mime};base64,{data}"


def trend_svg(history, w=820, h=280):
    """
    Amplitude, mean rate and positional delta against date.

    Three quantities on one pair of axes only works because each is
    independently normalised to the plot height; the shapes are comparable,
    the absolute positions are not. Each series therefore carries its own
    range in the legend rather than a shared y-axis that would be meaningless.
    """
    rows = sorted([h_ for h_ in history if h_.date is not None], key=lambda x: x.when)
    if len(rows) < 2:
        return ("<p style='color:#697687'>At least two recorded runs are needed "
                "before a trend can be drawn.</p>")

    xs = [r.date.timestamp() for r in rows]
    x0, x1 = min(xs), max(xs)
    span = max(x1 - x0, 1.0)
    pad_l, pad_r, pad_t, pad_b = 46, 130, 14, 34

    def sx(v):
        return pad_l + (v - x0) / span * (w - pad_l - pad_r)

    series = [("Peak amplitude", "max_amplitude", "#1f8a4c", "deg"),
              ("Mean rate", "mean_rate", "#1f6fd0", "s/d"),
              ("Positional delta", "delta_rate", "#d97a1f", "s/d")]

    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" '
             f'xmlns="http://www.w3.org/2000/svg" '
             f'style="border:1px solid #d8dee7;border-radius:6px;background:#fff">']
    for i in range(5):
        y = pad_t + i / 4 * (h - pad_t - pad_b)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w-pad_r}" y2="{y:.1f}" '
                     f'stroke="#eef1f5"/>')
    for r in rows:
        x = sx(r.date.timestamp())
        parts.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{h-pad_b}" '
                     f'stroke="#f4f6f9"/>')
    for r in (rows[0], rows[-1]):
        x = sx(r.date.timestamp())
        parts.append(f'<text x="{x:.1f}" y="{h-pad_b+15}" font-size="10" fill="#697687" '
                     f'text-anchor="middle">{r.when[:10]}</text>')

    legend_y = pad_t + 6
    for name, attr, colour, unit in series:
        pts = [(r.date.timestamp(), getattr(r, attr)) for r in rows
               if getattr(r, attr) == getattr(r, attr)]
        if len(pts) < 2:
            continue
        vals = [v for _, v in pts]
        lo, hi = min(vals), max(vals)
        rng = (hi - lo) or 1.0

        def sy(v):
            return h - pad_b - (v - lo) / rng * (h - pad_t - pad_b) * 0.86 - 10

        d = " ".join(("M" if i == 0 else "L") + f"{sx(t):.1f},{sy(v):.1f}"
                     for i, (t, v) in enumerate(pts))
        parts.append(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2"/>')
        for t, v in pts:
            parts.append(f'<circle cx="{sx(t):.1f}" cy="{sy(v):.1f}" r="3" '
                         f'fill="{colour}"/>')
        parts.append(f'<rect x="{w-pad_r+6}" y="{legend_y-8}" width="9" height="9" '
                     f'fill="{colour}"/>')
        parts.append(f'<text x="{w-pad_r+20}" y="{legend_y}" font-size="10" '
                     f'fill="#1b2028">{name}</text>')
        parts.append(f'<text x="{w-pad_r+20}" y="{legend_y+12}" font-size="9" '
                     f'fill="#697687">{lo:.1f} to {hi:.1f} {unit}</text>')
        legend_y += 34

    parts.append(f'<text x="{(w-pad_r)/2:.0f}" y="{h-6}" font-size="10" fill="#697687" '
                 f'text-anchor="middle">Each series is scaled to its own range -- '
                 f'compare shapes, not heights</text>')
    parts.append("</svg>")
    return "".join(parts)


def build_watch_report(path, watch, caliber=None, trends=None, notes=None,
                       photo_path=None, owner=""):
    """One printable page for a watch: what it is, and how it has behaved."""
    e = html.escape
    now = datetime.now().strftime("%d %B %Y")
    p = ["<!doctype html><html><head><meta charset='utf-8'>",
         f"<title>{e(watch.label)}</title><style>{CSS}"
         ".photo{float:right;max-width:270px;max-height:270px;border-radius:8px;"
         "border:1px solid #d8dee7;margin:0 0 12px 18px}"
         ".pill{display:inline-block;padding:2px 9px;border:1px solid var(--line);"
         "border-radius:11px;font-size:11px;color:var(--mut);margin-right:6px}"
         "</style></head><body>"]

    p.append(f"<h1>{e(watch.label)}</h1>")
    sub = [x for x in (watch.brand, watch.model, watch.reference) if x]
    p.append(f"<p class='sub'>{e(' &middot; '.join(sub))} &middot; {now}"
             f"{' &middot; ' + e(owner) if owner else ''}</p>".replace("&amp;middot;", "&middot;"))

    img = _embed_image(photo_path)
    if img:
        p.append(f"<img class='photo' src='{img}' alt='watch'>")

    # --- identity ---
    rows = [("Brand", watch.brand), ("Model", watch.model),
            ("Reference", watch.reference), ("Nickname", watch.nickname),
            ("Case serial", watch.serial), ("Movement serial", watch.movement_serial),
            ("Production year", watch.production_year),
            ("Case material", watch.material), ("Bezel", watch.bezel),
            ("Crystal", watch.crystal), ("Case size", watch.case_size_mm),
            ("Water resistance", watch.water_resistance),
            ("Bracelet / strap", watch.bracelet)]
    p.append("<h2>The watch</h2><table>")
    for k, v in rows:
        if v and str(v).strip():
            p.append(f"<tr><th style='width:190px'>{k}</th><td>{e(str(v))}</td></tr>")
    p.append("</table>")

    if caliber is not None:
        p.append("<h2>Movement</h2><table>")
        lift = watch.lift_angle or caliber.lift_angle
        mrows = [("Caliber", f"{caliber.brand} {caliber.name}"),
                 ("Beat rate", f"{caliber.bph} bph" if caliber.bph else "auto-detected"),
                 ("Lift angle", f"{lift:g} degrees"
                  + (" (override)" if watch.lift_angle else " (from caliber)")),
                 ("Regulating hardware",
                  __import__("watchgrapher.advisor", fromlist=["x"]).REGULATOR_LABELS.get(
                      caliber.regulator, caliber.regulator)),
                 ("Expected amplitude",
                  f"{caliber.amp_full_wind[0]:.0f}-{caliber.amp_full_wind[1]:.0f} degrees, "
                  f"dial up at full wind"),
                 ("Target rate", f"{watch.target_rate} s/day" if watch.target_rate else "")]
        for k, v in mrows:
            if v:
                p.append(f"<tr><th style='width:190px'>{k}</th><td>{e(str(v))}</td></tr>")
        p.append("</table>")
        if caliber.notes:
            p.append(f"<p class='sub'>{e(caliber.notes)}</p>")

    # --- provenance ---
    prov = [("Purchase date", watch.purchase_date),
            ("Purchase price", f"{watch.purchase_price} {watch.purchase_currency}"
             if watch.purchase_price else ""),
            ("Condition at purchase", watch.purchase_condition),
            ("Purchased from", watch.purchased_from),
            ("Last serviced", watch.last_service),
            ("Service interval", f"{watch.service_interval_years} years"
             if watch.service_interval_years else "")]
    if any(v for _, v in prov):
        p.append("<h2>Ownership and service</h2><table>")
        for k, v in prov:
            if v and str(v).strip():
                p.append(f"<tr><th style='width:190px'>{k}</th><td>{e(str(v))}</td></tr>")
        p.append("</table>")
        due = watch.service_due()
        if due:
            p.append(f"<div class='f warn'><div class='t'>Service</div>"
                     f"<div class='d'>{e(due)}</div></div>")

    # --- history ---
    hist = sorted(watch.history, key=lambda h: h.when)
    p.append(f"<h2>Timing history</h2>")
    if not hist:
        p.append("<p class='sub'>No runs recorded yet.</p>")
    else:
        p.append("<div style='clear:both'></div>")
        p.append(trend_svg(hist))
        p.append("<table><tr><th>Date</th><th class='n'>Mean rate s/d</th>"
                 "<th class='n'>Delta s/d</th><th class='n'>Amp max</th>"
                 "<th class='n'>Amp min</th><th class='n'>Beat err ms</th>"
                 "<th>Notes</th></tr>")
        for hrec in reversed(hist):
            tag = " <span class='pill'>post-service</span>" if hrec.service_event else ""
            p.append(f"<tr><td>{hrec.when[:16].replace('T', ' ')}</td>"
                     f"<td class='n'>{_fmt(hrec.mean_rate, 1)}</td>"
                     f"<td class='n'>{_fmt(hrec.delta_rate, 1)}</td>"
                     f"<td class='n'>{_fmt(hrec.max_amplitude, 0)}</td>"
                     f"<td class='n'>{_fmt(hrec.min_amplitude, 0)}</td>"
                     f"<td class='n'>{_fmt(hrec.max_beat_error, 2)}</td>"
                     f"<td>{e(hrec.notes)}{tag}</td></tr>")
        p.append("</table>")

        # most recent run, position by position
        last = hist[-1]
        if last.readings:
            p.append(f"<h2>Most recent run &mdash; {last.when[:16].replace('T', ' ')}</h2>")
            p.append("<table><tr><th>Position</th><th>Wind</th><th class='n'>Rate s/d</th>"
                     "<th class='n'>Amplitude</th><th class='n'>Beat error ms</th></tr>")
            for rd in last.readings:
                p.append(f"<tr><td>{e(str(rd.get('position', '')))}</td>"
                         f"<td>{e(str(rd.get('wind', '')))}</td>"
                         f"<td class='n'>{_fmt(rd.get('rate'), 1)}</td>"
                         f"<td class='n'>{_fmt(rd.get('amplitude'), 0)}</td>"
                         f"<td class='n'>{_fmt(rd.get('beat_error'), 2)}</td></tr>")
            p.append("</table>")

    if trends:
        p.append("<h2>Trend</h2>")
        for t in trends:
            sev = "good" if "Stable" in t.verdict else (
                "warn" if "watching" in t.verdict else "")
            extra = ("" if t.n == 0 else
                     f" &mdash; {t.first:.1f} to {t.last:.1f} {t.unit}, "
                     f"spread {t.stdev:.2f}")
            p.append(f"<div class='f {sev}'><div class='t'>{e(t.metric)}{extra}</div>"
                     f"<div class='d'>{e(t.verdict)}</div></div>")

    if notes:
        p.append("<h2>Observations</h2>")
        for n in notes:
            p.append(f"<div class='f'><div class='d'>{e(n)}</div></div>")

    if watch.notes:
        p.append(f"<h2>Notes</h2><p>{e(watch.notes).replace(chr(10), '<br>')}</p>")

    p.append("<div class='foot'>Measured acoustically with WatchGrapher. Amplitude "
             "accuracy is limited by how well the lift angle is known; rate and beat "
             "error are not affected by it. Bench figures are not wrist performance."
             "</div></body></html>")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(p))
    return os.path.abspath(path)
