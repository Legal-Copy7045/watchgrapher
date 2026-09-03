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


def html_to_pdf(html, pdf_path, title=""):
    """
    Render an HTML report to PDF with Qt's own engine -- no extra dependency.
    `html` may be a path to an .html file or an HTML string. QTextDocument
    supports a CSS 2.1 subset, so flex/grid card rows fall back to stacked
    blocks; tables, colours and the SVG images survive. Returns pdf_path.
    """
    from PySide6 import QtGui, QtCore
    if os.path.exists(str(html)):
        with open(html, encoding="utf-8") as fh:
            html_str = fh.read()
        base = QtCore.QUrl.fromLocalFile(os.path.abspath(html) + "/")
    else:
        html_str, base = str(html), QtCore.QUrl()

    doc = QtGui.QTextDocument()
    doc.setMetaInformation(QtGui.QTextDocument.DocumentTitle, title or "Report")
    if not base.isEmpty():
        doc.setBaseUrl(base)
    doc.setHtml(html_str)

    writer = QtGui.QPdfWriter(str(pdf_path))
    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    writer.setPageMargins(QtCore.QMarginsF(14, 14, 14, 14),
                          QtGui.QPageLayout.Millimeter)
    writer.setResolution(150)
    doc.setPageSize(QtCore.QSizeF(writer.pageLayout().paintRectPixels(
        writer.resolution()).size()))
    doc.print_(writer)
    return str(pdf_path)


def _fmt(v, dp=1, dash="--", signed=False):
    try:
        if v != v:
            return dash
        return f"{v:+.{dp}f}" if signed else f"{v:.{dp}f}"
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


CERT_CSS = CSS + """
.cert{border:2px solid var(--ink);padding:28px 34px;margin-top:10px}
.cert h1{font-size:20px;letter-spacing:.12em;text-transform:uppercase;text-align:center}
.cert .std{text-align:center;color:var(--mut);letter-spacing:.08em;margin:2px 0 18px}
.verdict{text-align:center;font-size:26px;font-weight:800;letter-spacing:.15em;
         padding:10px;margin:12px 0;border:2px solid currentColor}
.idg{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;margin:14px 0}
.idg div:nth-child(odd){color:var(--mut)}
.sig{display:grid;grid-template-columns:1fr 1fr;gap:40px;margin-top:34px}
.sig div{border-top:1px solid var(--ink);padding-top:6px;color:var(--mut);font-size:12px}
"""


def build_certificate(path, *, caliber, readings, grade, watch_label="",
                      serial="", technician="", notes="", owner=""):
    """A single-page COSC/METAS-style timing certificate."""
    e = html.escape
    now = datetime.now().strftime("%d %B %Y")
    passed = bool(grade and grade.get("passed"))
    rows = (grade or {}).get("rows", [])
    standard = (grade or {}).get("standard", "")
    colour = "var(--good)" if passed else "var(--bad)"
    verdict = "CONFORMS" if passed else "OUTSIDE SPECIFICATION"

    p = ["<!doctype html><html><head><meta charset='utf-8'>",
         f"<title>Timing certificate -- {e(watch_label or (caliber.label if caliber else 'watch'))}"
         f"</title><style>{CERT_CSS}</style></head><body><div class='cert'>"]
    p.append("<h1>Timing Certificate</h1>")
    p.append(f"<p class='std'>Assessed against: {e(standard) or 'no standard selected'}</p>")

    ident = [("Watch", watch_label or "--"),
             ("Movement", caliber.label if caliber else "--"),
             ("Beat rate", f"{caliber.bph} bph" if caliber and caliber.bph else "auto-detected"),
             ("Lift angle", f"{caliber.lift_angle:g}°" if caliber else "--"),
             ("Serial", serial or "--"), ("Owner", owner or "--"),
             ("Date of test", now), ("Tested by", technician or "--")]
    p.append("<div class='idg'>")
    for k, v in ident:
        p.append(f"<div>{e(k)}</div><div>{e(str(v))}</div>")
    p.append("</div>")

    p.append(f"<div class='verdict' style='color:{colour}'>{verdict}</div>")

    if rows:
        p.append("<table><tr><th>Criterion</th><th class='n'>Measured</th>"
                 "<th class='n'>Limit</th><th>Result</th></tr>")
        for r in rows:
            rc = "var(--good)" if r.ok else "var(--bad)"
            p.append(f"<tr><td>{e(r.name)}</td><td class='n'>{e(r.value)}</td>"
                     f"<td class='n'>{e(r.limit)}</td>"
                     f"<td style='color:{rc}'>{'pass' if r.ok else 'fail'}</td></tr>")
        p.append("</table>")

    if readings:
        p.append("<h2>Positional measurements</h2><table><tr><th>Position</th><th>Wind</th>"
                 "<th class='n'>Rate s/d</th><th class='n'>Amplitude</th>"
                 "<th class='n'>Beat error ms</th></tr>")
        rates = [r.rate for r in readings if r.rate == r.rate]
        amps = [r.amplitude for r in readings if r.amplitude == r.amplitude]
        for r in readings:
            p.append(f"<tr><td>{e(r.position)}</td><td>{e(r.wind_state)}</td>"
                     f"<td class='n'>{_fmt(r.rate, 1, signed=True)}</td>"
                     f"<td class='n'>{_fmt(r.amplitude, 0)}</td>"
                     f"<td class='n'>{_fmt(r.beat_error, 2)}</td></tr>")
        if len(rates) >= 2:
            p.append(f"<tr><td colspan='2'><b>Mean rate / delta</b></td>"
                     f"<td class='n'><b>{sum(rates)/len(rates):+.1f} / "
                     f"{max(rates)-min(rates):.1f}</b></td>"
                     f"<td class='n'><b>{f'{max(amps)-min(amps):.0f} drop' if len(amps)>=2 else '--'}</b>"
                     f"</td><td></td></tr>")
        p.append("</table>")

    if notes:
        p.append(f"<h2>Notes</h2><p>{e(notes)}</p>")
    p.append("<p class='sub' style='margin-top:20px;font-size:11px'>Indicative "
             "acoustic assessment from a single six-position run. Not a certified "
             "laboratory test and not affiliated with COSC, METAS or any observatory.</p>")
    p.append("<div class='sig'><div>Signature</div><div>Date</div></div>")
    p.append("</div></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(p))
    return path


def build_before_after(path, *, watch, service, before, after, caliber=None):
    """One page comparing timing runs either side of a service."""
    e = html.escape

    def g(rec, attr):
        v = getattr(rec, attr, float("nan")) if rec else float("nan")
        return v if v == v else None

    rows = [
        ("Mean rate", "mean_rate", "s/d", 1, True, "lower magnitude is better"),
        ("Positional delta", "delta_rate", "s/d", 1, False, "smaller is better"),
        ("Amplitude, highest", "max_amplitude", "°", 0, False, "higher is better, to a point"),
        ("Amplitude, lowest", "min_amplitude", "°", 0, False, "higher is better"),
        ("Beat error, worst", "max_beat_error", "ms", 2, False, "smaller is better"),
    ]
    p = ["<!doctype html><html><head><meta charset='utf-8'>",
         f"<title>Before / after -- {e(watch.label)}</title><style>{CSS}</style></head><body>"]
    p.append(f"<h1>Service before / after &mdash; {e(watch.label)}</h1>")
    p.append(f"<p class='sub'>{e((caliber.label + ' &middot; ') if caliber else '')}"
             f"{e(service.kind)} on {e(service.when)}"
             f"{' &middot; ' + e(service.performed_by) if service.performed_by else ''}</p>")
    p.append(f"<p class='sub'>Before run: {e(before.when[:16].replace('T',' ')) if before else 'none found'}"
             f" &nbsp;&rarr;&nbsp; After run: "
             f"{e(after.when[:16].replace('T',' ')) if after else 'none found'}</p>")

    p.append("<table><tr><th>Metric</th><th class='n'>Before</th><th class='n'>After</th>"
             "<th class='n'>Change</th><th></th></tr>")
    for name, attr, unit, dp, signed, hint in rows:
        b, a = g(before, attr), g(after, attr)
        sgn = "+" if signed else ""
        bs = f"{b:{sgn}.{dp}f} {unit}" if b is not None else "--"
        as_ = f"{a:{sgn}.{dp}f} {unit}" if a is not None else "--"
        ch = f"{a - b:+.{dp}f} {unit}" if (b is not None and a is not None) else "--"
        p.append(f"<tr><td>{e(name)}</td><td class='n'>{bs}</td><td class='n'>{as_}</td>"
                 f"<td class='n'>{ch}</td><td class='sub'>{e(hint)}</td></tr>")
    p.append("</table>")

    notes = []
    ba, aa = g(before, "min_amplitude"), g(after, "min_amplitude")
    if ba is not None and aa is not None:
        if aa - ba > 15:
            notes.append(f"Amplitude recovered {aa - ba:.0f}° at its lowest position -- "
                         f"the service freed the train.")
        elif aa - ba < -15:
            notes.append(f"Amplitude dropped {ba - aa:.0f}° -- check mainspring choice, "
                         f"barrel-wall lubrication and escapement oiling.")
    bd, ad = g(before, "delta_rate"), g(after, "delta_rate")
    if bd is not None and ad is not None and bd - ad > 5:
        notes.append(f"Positional delta tightened from {bd:.0f} to {ad:.0f} s/d.")
    bb, ab = g(before, "max_beat_error"), g(after, "max_beat_error")
    if bb is not None and ab is not None and bb - ab > 0.2:
        notes.append(f"Beat error set from {bb:.2f} to {ab:.2f} ms.")
    if notes:
        p.append("<h2>Summary</h2>")
        for n in notes:
            p.append(f"<div class='f good'><div class='d'>{e(n)}</div></div>")

    for tag, rec in (("Before", before), ("After", after)):
        if rec and getattr(rec, "readings", None):
            p.append(f"<h2>{tag} &mdash; positions</h2><table><tr><th>Position</th>"
                     "<th class='n'>Rate</th><th class='n'>Amplitude</th>"
                     "<th class='n'>Beat error</th></tr>")
            for rd in rec.readings:
                p.append(f"<tr><td>{e(str(rd.get('position','')))}</td>"
                         f"<td class='n'>{_fmt(rd.get('rate'), 1)}</td>"
                         f"<td class='n'>{_fmt(rd.get('amplitude'), 0)}</td>"
                         f"<td class='n'>{_fmt(rd.get('beat_error'), 2)}</td></tr>")
            p.append("</table>")

    p.append("</body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(p))
    return path


def build(path, caliber, readings, measurement=None, findings=None,
          fault_report=None, tuning=None, reserve_log=None,
          watch_label="", technician="", notes="", grade=None):
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
        try:
            from .analysis import escapement_metrics
            em = escapement_metrics(m.amplitude, m.lift_angle)
            if em.rating:
                p.append(f"<p class='sub'>Escapement impulse fraction "
                         f"{em.impulse_fraction:.1f}% ({e(em.rating)}) &mdash; "
                         f"{em.free_arc_deg:.0f}&deg; of free swing per beat. "
                         f"{e(em.note)}</p>")
        except Exception:
            pass
        p.append(trace_svg(m, m.nominal_bph or m.detected_bph))

    if readings:
        p.append("<h2>Positional results</h2><table><tr><th>Position</th><th>Wind</th>"
                 "<th class='n'>Rate s/d</th><th class='n'>Amplitude</th>"
                 "<th class='n'>Beat error ms</th></tr>")
        for r in readings:
            p.append(f"<tr><td>{e(r.position)}</td><td>{e(r.wind_state)}</td>"
                     f"<td class='n'>{_fmt(r.rate, 1, signed=True)}</td>"
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

    if grade and grade.get("rows"):
        verdict = "PASS" if grade["passed"] else "OUTSIDE SPEC"
        colour = "var(--good)" if grade["passed"] else "var(--bad)"
        p.append(f"<h2>Certificate &mdash; {e(grade['standard'])}</h2>")
        p.append(f"<p class='sub'><b style='color:{colour}'>{verdict}</b> &nbsp; "
                 f"Indicative grading from this six-position run, not a certified "
                 f"laboratory test.</p>")
        p.append("<table><tr><th>Criterion</th><th class='n'>Measured</th>"
                 "<th class='n'>Limit</th><th>Result</th></tr>")
        for r in grade["rows"]:
            rc = "var(--good)" if r.ok else "var(--bad)"
            p.append(f"<tr><td>{e(r.name)}</td><td class='n'>{e(r.value)}</td>"
                     f"<td class='n'>{e(r.limit)}</td>"
                     f"<td style='color:{rc}'>{'pass' if r.ok else 'fail'}</td></tr>")
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


def _schematic_uri(obj):
    """A generated schematic illustration of a watch as an SVG data URI."""
    try:
        from .watchart import watch_svg_for
        svg = watch_svg_for(obj, size=520).encode("utf-8")
        return "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")
    except Exception:
        return None


def _watch_image(obj, photo_path):
    """The watch's own photo if there is one, else a generated schematic."""
    return _embed_image(photo_path) or _schematic_uri(obj)


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


def _service_section(watch, doc_dir=""):
    e = html.escape
    svcs = sorted(getattr(watch, "services", []), key=lambda s: s.when, reverse=True)
    if not svcs:
        return ""
    out = ["<h2>Service history</h2>"]
    totals = watch.total_service_cost() if hasattr(watch, "total_service_cost") else {}
    if totals:
        out.append("<p class='sub'>Total recorded spend: "
                   + ", ".join(f"{v:.0f} {k}" for k, v in totals.items()) + "</p>")
    out.append("<table><tr><th>Date</th><th>Type</th><th>By</th><th class='n'>Cost</th>"
               "<th>Warranty</th><th>Water resistance</th><th>Notes</th></tr>")
    for s in svcs:
        cost = f"{s.cost} {s.currency}" if s.cost else "--"
        warr = f"{s.warranty_months} mo" if s.warranty_months else ""
        loc = f" &mdash; {e(s.location)}" if s.location else ""
        wr = getattr(s, "wr_summary", "") or "--"
        wr_extra = f" @ {e(s.wr_pressure)}" if getattr(s, "wr_pressure", "") else ""
        out.append(f"<tr><td>{e(s.when)}</td><td>{e(s.kind)}</td>"
                   f"<td>{e(s.performed_by)}{loc}</td><td class='n'>{e(cost)}</td>"
                   f"<td>{e(warr)}</td><td>{e(wr)}{wr_extra}</td><td>{e(s.notes)}</td></tr>")
    out.append("</table>")
    for s in svcs:
        imgs = [_embed_image(os.path.join(doc_dir, d)) for d in s.documents
                if doc_dir and os.path.splitext(d)[1].lower() in
                (".png", ".jpg", ".jpeg", ".webp", ".bmp")]
        imgs = [x for x in imgs if x]
        others = [d for d in s.documents
                  if os.path.splitext(d)[1].lower() not in
                  (".png", ".jpg", ".jpeg", ".webp", ".bmp")]
        if imgs or others:
            out.append(f"<h3 style='margin:14px 0 4px;font-size:13px'>"
                       f"{e(s.when)} &mdash; {e(s.kind)} &mdash; attachments</h3>")
        for src in imgs:
            out.append(f"<img src='{src}' style='max-width:100%;max-height:520px;"
                       f"border:1px solid #d8dee7;border-radius:6px;margin:6px 0'>")
        for d in others:
            out.append(f"<p class='sub'>Document on file: {e(d)}</p>")
    return "".join(out)


def _reserve_section(watch):
    e = html.escape
    runs = sorted(getattr(watch, "reserves", []), key=lambda r: r.when, reverse=True)
    if not runs:
        return ""
    out = ["<h2>Power reserve runs</h2>",
           "<table><tr><th>Date</th><th class='n'>Power reserve</th>"
           "<th class='n'>Good time to</th><th class='n'>Run length</th>"
           "<th class='n'>Amplitude start &rarr; end</th><th>Isochronism</th></tr>"]
    for r in runs:
        amp = (f"{r.amp_first:.0f} &rarr; {r.amp_last:.0f}"
               if r.amp_first == r.amp_first else "--")
        pr = getattr(r, "power_reserve_h", float("nan"))
        prac = getattr(r, "practical_h", float("nan"))
        if not (pr == pr) and r.hours_to_200 == r.hours_to_200:
            prac = prac if prac == prac else r.hours_to_200
        est = " est" if getattr(r, "pr_estimated", False) else ""
        pr_txt = (f"~{pr:.0f} h{est}" if pr == pr else "--")
        prac_txt = (f"{prac:.0f} h" if prac == prac else "--")
        if r.iso_span == r.iso_span:
            mag = abs(r.iso_span)
            iso = (f"{'good' if mag < 4 else 'fair' if mag < 12 else 'poor'} "
                   f"({r.iso_span:+.1f} s/d across the range)")
        else:
            iso = "--"
        note = " &mdash; stopped early" if r.stopped_early else ""
        out.append(f"<tr><td>{e(r.when[:16].replace('T', ' '))}{note}</td>"
                   f"<td class='n'>{pr_txt}</td><td class='n'>{prac_txt}</td>"
                   f"<td class='n'>{_fmt(r.hours, 1)}</td>"
                   f"<td class='n'>{amp}</td><td>{iso}</td></tr>")
    out.append("</table>")
    out.append("<p class='sub'>Power reserve is full wind to the watch running "
               "down (~135&deg;); 'good time to' is when amplitude reaches 200&deg;. "
               "'est' means projected from the decay rather than reached in the run.</p>")
    return "".join(out)


def build_watch_report(path, watch, caliber=None, trends=None, notes=None,
                       photo_path=None, owner="", doc_dir=""):
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
    sub = [e(x) for x in (watch.brand, watch.model, watch.reference, now,
                          owner) if x]
    p.append(f"<p class='sub'>{' &middot; '.join(sub)}</p>")

    img = _watch_image(watch, photo_path)
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

    try:
        from .analysis import history_stability
        hs = history_stability([(h.date, h.mean_rate) for h in hist if h.date])
        if hs.n >= 2:
            p.append("<h2>Long-term rate stability</h2>")
            p.append(f"<p class='sub'>{e(hs.verdict)}</p>")
            if hs.dev:
                p.append("<table><tr><th>Runs averaged</th>"
                         "<th class='n'>Rate deviation s/day</th></tr>")
                for tau, d in zip(hs.taus, hs.dev):
                    p.append(f"<tr><td>{tau}</td><td class='n'>{d:.2f}</td></tr>")
                p.append("</table>")
    except Exception:
        pass

    if getattr(watch, "regulation_log", None):
        p.append("<h2>Regulation log</h2>")
        try:
            from .collection import regulation_sensitivity
            s = regulation_sensitivity(watch)
            if s:
                p.append(f"<p class='sub'>Learned index sensitivity: "
                         f"<b>{s['spd_per_unit']:+.1f} s/day per {e(s['unit'])}</b> "
                         f"(from {s['n']} adjustment(s)"
                         + (f", &plusmn;{s['scatter']:.1f}" if s['scatter'] else "")
                         + ").</p>")
        except Exception:
            pass
        p.append("<table><tr><th>Date</th><th class='n'>Before</th>"
                 "<th class='n'>After</th><th>Move</th><th>Note</th></tr>")
        for r in sorted(watch.regulation_log, key=lambda x: x.get("when", ""),
                        reverse=True):
            amt = f" ({r['amount']:+g} {e(r.get('unit',''))})" if r.get("amount") else ""
            p.append(f"<tr><td>{e(r.get('when','')[:10])}</td>"
                     f"<td class='n'>{_fmt(r.get('before'), 1, signed=True)}</td>"
                     f"<td class='n'>{_fmt(r.get('after'), 1, signed=True)}</td>"
                     f"<td>{e(r.get('move',''))}{amt}</td>"
                     f"<td>{e(r.get('note',''))}</td></tr>")
        p.append("</table>")

    p.append(_reserve_section(watch))
    p.append(_service_section(watch, doc_dir))

    vault = getattr(watch, "documents", [])
    if vault:
        p.append("<h2>Documents on file</h2><table><tr><th>Kind</th><th>Name</th>"
                 "<th>Added</th><th>Note</th></tr>")
        for d in sorted(vault, key=lambda x: x.get("added", ""), reverse=True):
            p.append(f"<tr><td>{e(d.get('kind',''))}</td><td>{e(d.get('name',''))}</td>"
                     f"<td>{e(d.get('added','')[:10])}</td><td>{e(d.get('note',''))}</td></tr>")
        p.append("</table>")

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


def build_comparison(path, watches, collection=None, owner=""):
    """Side-by-side comparison of two to four watches."""
    e = html.escape
    now = datetime.now().strftime("%d %B %Y")
    from .calibers import CALIBERS
    from .collection import summarise

    ws = list(watches)[:4]
    ncol = len(ws)
    p = ["<!doctype html><html><head><meta charset='utf-8'>",
         f"<title>Comparison</title><style>{CSS}"
         "img.cmp{max-width:150px;max-height:150px;border:1px solid var(--line);"
         "border-radius:6px;background:#fff}"
         "td,th{vertical-align:top}</style></head><body>"]
    p.append("<h1>Watch comparison</h1>")
    p.append(f"<p class='sub'>{ncol} watches &middot; {now}"
             f"{' &middot; ' + e(owner) if owner else ''}</p>")

    def latest(w):
        h = sorted(w.history, key=lambda x: x.when)
        return h[-1] if h else None

    def row(label, fn):
        cells = "".join(f"<td>{fn(w) or '--'}</td>" for w in ws)
        return f"<tr><th style='width:170px'>{e(label)}</th>{cells}</tr>"

    p.append("<table><tr><th></th>"
             + "".join(f"<th>{e(w.label)}</th>" for w in ws) + "</tr>")
    try:
        from .watchart import watch_svg_for
        p.append("<tr><th></th>" + "".join(
            "<td><img class='cmp' src='data:image/svg+xml;base64,"
            + base64.b64encode(watch_svg_for(w, size=300).encode()).decode()
            + "'></td>" for w in ws) + "</tr>")
    except Exception:
        pass
    p.append(row("Reference", lambda w: e(w.reference)))
    p.append(row("Movement", lambda w: e((lambda c: f"{c.brand} {c.name}" if c else "")(
        CALIBERS.get(w.caliber_key)))))
    p.append(row("Case", lambda w: e(" / ".join(x for x in (w.material, w.bezel) if x))))
    p.append(row("Runs on record", lambda w: str(len(w.history))))

    def lat_fmt(attr, dp, signed=False):
        def f(w):
            r = latest(w)
            v = getattr(r, attr, None) if r else None
            return _fmt(v, dp, signed=signed) if v is not None else "--"
        return f
    p.append(row("Latest mean rate", lat_fmt("mean_rate", 1, True)))
    p.append(row("Latest positional delta", lat_fmt("delta_rate", 1)))
    p.append(row("Latest amplitude (max)", lat_fmt("max_amplitude", 0)))
    p.append(row("Latest amplitude (min)", lat_fmt("min_amplitude", 0)))
    p.append(row("Latest beat error", lat_fmt("max_beat_error", 2)))

    def trend_txt(w):
        return "<br>".join(f"{e(t.metric)}: {e(t.verdict)}"
                           for t in summarise(w) if t.n >= 3) or "not enough runs"
    p.append(row("Trend", trend_txt))
    p.append(row("Services", lambda w: str(len(w.services))))
    p.append(row("Service due", lambda w: e(w.service_due() or "unknown")))
    p.append(row("Tags", lambda w: e(", ".join(w.tags))))
    p.append("</table>")
    p.append("<div class='foot'>Generated by WatchGrapher. Bench figures, not "
             "wrist performance.</div></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(p))
    return os.path.abspath(path)


def build_portfolio(path, collection, owner=""):
    """One page covering the whole collection: what each watch is, and how it runs."""
    from .calibers import CALIBERS
    from .collection import summarise
    e = html.escape
    now = datetime.now().strftime("%d %B %Y")
    watches = collection.sorted_watches()

    p = ["<!doctype html><html><head><meta charset='utf-8'>",
         f"<title>Watch portfolio</title><style>{CSS}"
         ".wcard{border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:14px 0}"
         ".wcard h3{margin:0 0 2px;font-size:16px}"
         ".thumb{float:right;max-width:150px;max-height:150px;border-radius:6px;"
         "border:1px solid var(--line);background:#fff;margin:0 0 10px 14px}"
         "</style></head><body>"]
    p.append("<h1>Watch portfolio</h1>")
    p.append(f"<p class='sub'>{len(watches)} watch{'es' if len(watches) != 1 else ''} "
             f"&middot; {now}{' &middot; ' + e(owner) if owner else ''}</p>")

    def _money(s):
        try:
            return float(str(s).replace(",", "").replace("£", "").replace("$", "")
                         .replace("€", "").strip())
        except (ValueError, TypeError):
            return None

    buy, svc = {}, {}
    for w in watches:
        v = _money(w.purchase_price)
        if v is not None:
            buy[w.purchase_currency or "GBP"] = buy.get(w.purchase_currency or "GBP", 0.0) + v
        for cur, amt in (w.total_service_cost() if hasattr(w, "total_service_cost") else {}).items():
            svc[cur] = svc.get(cur, 0.0) + amt
    tot_bits = []
    if buy:
        tot_bits.append("purchases " + ", ".join(f"{v:,.0f} {k}" for k, v in buy.items()))
    if svc:
        tot_bits.append("service spend " + ", ".join(f"{v:,.0f} {k}" for k, v in svc.items()))
    if tot_bits:
        p.append(f"<p class='sub'>{' &middot; '.join(tot_bits)}</p>")

    p.append("<h2>At a glance</h2><table><tr><th>Watch</th><th>Movement</th>"
             "<th class='n'>Runs</th><th class='n'>Latest rate</th>"
             "<th class='n'>Latest amp</th><th>Last service</th><th>Due</th></tr>")
    for w in watches:
        c = CALIBERS.get(w.caliber_key)
        hist = sorted(w.history, key=lambda h: h.when)
        last = hist[-1] if hist else None
        rate = (_fmt(last.mean_rate, 1) + " s/d") if last else "--"
        amp = _fmt(last.max_amplitude, 0) if last else "--"
        due = w.service_due() or ""
        p.append(f"<tr><td>{e(w.label)}</td>"
                 f"<td>{e(c.brand + ' ' + c.name) if c else 'not set'}</td>"
                 f"<td class='n'>{len(w.history)}</td>"
                 f"<td class='n'>{rate}</td><td class='n'>{amp}</td>"
                 f"<td>{e(w.effective_last_service or '--')}</td>"
                 f"<td>{'past due' if 'past the' in due else ('ok' if due else '')}</td></tr>")
    p.append("</table>")

    for w in watches:
        c = CALIBERS.get(w.caliber_key)
        p.append("<div class='wcard'>")
        img = _watch_image(
            w, collection.photo_path(w) if hasattr(collection, "photo_path") else None)
        if img:
            p.append(f"<img class='thumb' src='{img}'>")
        p.append(f"<h3>{e(w.label)}</h3>")
        idbits = [x for x in (w.brand, w.model, w.reference,
                              f"serial {w.serial}" if w.serial else "",
                              w.production_year) if x]
        p.append(f"<p class='sub'>{e(' | '.join(idbits))}</p>")

        rows = [("Movement", f"{c.brand} {c.name}" if c else "not set"),
                ("Lift angle", f"{(w.lift_angle or (c.lift_angle if c else 52)):g} deg"),
                ("Case", " / ".join(x for x in (w.material, w.bezel, w.crystal,
                                                w.case_size_mm) if x)),
                ("Purchased", " ".join(x for x in (
                    w.purchase_date,
                    f"for {w.purchase_price} {w.purchase_currency}" if w.purchase_price else "",
                    f"from {w.purchased_from}" if w.purchased_from else "") if x)),
                ("Target rate", f"{w.target_rate} s/d" if w.target_rate else "")]
        p.append("<table>")
        for k, v in rows:
            if v and str(v).strip():
                p.append(f"<tr><th style='width:150px'>{k}</th><td>{e(str(v))}</td></tr>")
        p.append("</table>")

        hist = sorted(w.history, key=lambda h: h.when)
        if hist:
            last = hist[-1]
            p.append(f"<p><b>{len(hist)} run(s).</b> Latest ({last.when[:10]}): "
                     f"rate {_fmt(last.mean_rate, 1)} s/d, amplitude "
                     f"{_fmt(last.max_amplitude, 0)}&ndash;{_fmt(last.min_amplitude, 0)} deg, "
                     f"beat error {_fmt(last.max_beat_error, 2)} ms, "
                     f"positional delta {_fmt(last.delta_rate, 1)} s/d.</p>")
            for t in summarise(w):
                if t.n >= 3:
                    p.append(f"<p class='sub'>{e(t.metric)}: {e(t.verdict)}</p>")
        else:
            p.append("<p class='sub'>No timing runs recorded yet.</p>")

        rsv = sorted(getattr(w, "reserves", []), key=lambda r: r.when, reverse=True)
        if rsv:
            last = rsv[0]
            prh = getattr(last, "power_reserve_h", float("nan"))
            prtxt = (f"power reserve ~{prh:.0f} h"
                     + (" (est)" if getattr(last, "pr_estimated", False) else "")
                     if prh == prh else f"{_fmt(last.hours, 1)} h run")
            p.append(f"<p><b>{len(rsv)} power-reserve run(s).</b> Most recent "
                     f"({last.when[:10]}): {prtxt}, amplitude "
                     f"{_fmt(last.amp_first, 0)}&ndash;{_fmt(last.amp_last, 0)} deg"
                     + (f", isochronism {last.iso_span:+.1f} s/d across the range"
                        if last.iso_span == last.iso_span else "") + ".</p>")

        svcs = sorted(getattr(w, "services", []), key=lambda s: s.when, reverse=True)
        if svcs:
            tc = ", ".join(f"{v:.0f} {k}" for k, v in w.total_service_cost().items())
            p.append(f"<p><b>{len(svcs)} service(s)</b>"
                     + (f", total {tc}" if tc else "") + ". Most recent: "
                     f"{e(svcs[0].when)} {e(svcs[0].kind)}"
                     + (f" by {e(svcs[0].performed_by)}" if svcs[0].performed_by else "") + ".</p>")
        due = w.service_due()
        if due:
            p.append(f"<div class='f {'warn' if 'past the' in due else ''}'>"
                     f"<div class='d'>{e(due)}</div></div>")
        p.append("</div>")

    p.append("<div class='foot'>Generated by WatchGrapher. Timing figures are bench "
             "measurements, not wrist performance.</div></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(p))
    return os.path.abspath(path)


def build_year_review(path, collection, year: int, owner=""):
    """Everything that happened to the collection in one calendar year."""
    e = html.escape
    y0, y1 = f"{year}-01-01", f"{year}-12-31T23:59:59"
    watches = collection.sorted_watches()

    def in_year(iso):
        return bool(iso) and y0 <= str(iso)[:19] <= y1

    acquired = [w for w in watches if in_year(w.purchase_date)]
    n_services = n_runs = n_reserves = n_wear = 0
    spend = {}
    lines_svc, lines_run = [], []
    for w in watches:
        for s in w.services:
            if in_year(s.when):
                n_services += 1
                v = s.cost_value
                if v is not None:
                    spend[s.currency or "GBP"] = spend.get(s.currency or "GBP", 0.0) + v
                wr = f" &middot; WR {e(s.wr_summary)}" if getattr(s, "wr_summary", "") else ""
                lines_svc.append(f"<tr><td>{e(s.when)}</td><td>{e(w.label)}</td>"
                                 f"<td>{e(s.kind)}</td><td>{e(s.performed_by)}</td>"
                                 f"<td class='n'>{e(s.cost)} {e(s.currency) if s.cost else ''}</td>"
                                 f"<td>{e(s.notes[:80])}{wr}</td></tr>")
        yr_runs = [h for h in w.history if in_year(h.when)]
        n_runs += len(yr_runs)
        if yr_runs:
            latest = max(yr_runs, key=lambda h: h.when)
            lines_run.append(f"<tr><td>{e(w.label)}</td><td class='n'>{len(yr_runs)}</td>"
                             f"<td class='n'>{_fmt(latest.mean_rate, 1)}</td>"
                             f"<td class='n'>{_fmt(latest.delta_rate, 1)}</td>"
                             f"<td class='n'>{_fmt(latest.min_amplitude, 0)}</td>"
                             f"<td>{e(latest.when[:10])}</td></tr>")
        n_reserves += sum(1 for r in w.reserves if in_year(r.when))
        n_wear += sum(1 for c in getattr(w, "wear_checks", []) if in_year(c.get("when", "")))

    p = ["<!doctype html><html><head><meta charset='utf-8'>",
         f"<title>{year} in review</title><style>{CSS}</style></head><body>"]
    p.append(f"<h1>{year} in review</h1>")
    p.append(f"<p class='sub'>{len(watches)} watches in the collection"
             f"{' &middot; ' + e(owner) if owner else ''}</p>")
    p.append("<div class='cards'>")
    spend_s = ", ".join(f"{v:.0f} {k}" for k, v in spend.items()) or "--"
    for k, v in (("Watches acquired", len(acquired)), ("Services", n_services),
                 ("Service spend", spend_s), ("Timing runs", n_runs),
                 ("Power-reserve runs", n_reserves), ("Wrist-rate checks", n_wear)):
        p.append(f"<div class='card'><div class='k'>{k}</div><div class='v'>{v}</div></div>")
    p.append("</div>")

    if acquired:
        p.append("<h2>Acquired this year</h2><table><tr><th>Watch</th><th>Date</th>"
                 "<th class='n'>Price</th><th>From</th></tr>")
        for w in acquired:
            p.append(f"<tr><td>{e(w.label)}</td><td>{e(w.purchase_date)}</td>"
                     f"<td class='n'>{e(w.purchase_price)} {e(w.purchase_currency) if w.purchase_price else ''}</td>"
                     f"<td>{e(w.purchased_from)}</td></tr>")
        p.append("</table>")

    if lines_svc:
        p.append("<h2>Services</h2><table><tr><th>Date</th><th>Watch</th><th>Type</th>"
                 "<th>By</th><th class='n'>Cost</th><th>Notes</th></tr>")
        p += sorted(lines_svc)
        p.append("</table>")

    if lines_run:
        p.append("<h2>Timing activity</h2><table><tr><th>Watch</th><th class='n'>Runs</th>"
                 "<th class='n'>Latest mean s/d</th><th class='n'>Latest delta</th>"
                 "<th class='n'>Latest amp low</th><th>Last run</th></tr>")
        p += lines_run
        p.append("</table>")

    if not (acquired or lines_svc or lines_run):
        p.append(f"<p class='sub'>Nothing recorded for {year}.</p>")

    p.append("<div class='foot'>Generated by WatchGrapher.</div></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(p))
    return os.path.abspath(path)
