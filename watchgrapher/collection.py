"""
Watch collection: profiles, test history, and performance trends.

A single timing run tells you how a watch is behaving today. Repeated runs
tell you whether it is drifting, which is the question that actually decides
when a service is due. Amplitude falling 15 degrees a year is invisible in any
one measurement and unmistakable across six.

Storage is one JSON file plus a photos folder, both inside the application
directory. Plain text on purpose -- a collection record outlives the software
that made it, and you should be able to read it with a text editor in ten
years.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

CURRENCIES = ["GBP", "USD", "EUR", "CHF", "JPY", "AUD", "CAD", "SEK", "NOK", "DKK"]
CONDITIONS = ["New / unworn", "Mint", "Excellent", "Very good", "Good", "Fair",
              "For parts or restoration", "Unknown"]
MATERIALS = ["Stainless steel", "Oystersteel", "Titanium", "Yellow gold",
             "White gold", "Rose / Everose gold", "Platinum", "Two-tone / Rolesor",
             "Bronze", "Ceramic", "Gold plated", "Chrome plated base metal",
             "Silver", "Carbon composite", "Unknown"]
CRYSTALS = ["Sapphire", "Acrylic / Hesalite", "Hardlex", "Mineral", "Unknown"]


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class TestRecord:
    """One timing session."""
    when: str = ""
    caliber_key: str = ""
    lift_angle: float = 52.0
    # Per-position results: [{position, wind, rate, amplitude, beat_error}]
    readings: List[dict] = field(default_factory=list)
    # Summary, computed at save time so history stays readable on its own.
    mean_rate: float = float("nan")
    delta_rate: float = float("nan")
    max_amplitude: float = float("nan")
    min_amplitude: float = float("nan")
    max_beat_error: float = float("nan")
    service_event: bool = False       # mark a run taken right after a service
    notes: str = ""

    @property
    def date(self) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(self.when)
        except (ValueError, TypeError):
            return None


SERVICE_KINDS = ["Full service", "Partial service", "Regulation only", "Repair",
                 "Warranty service", "Water resistance test", "Other"]

DOCUMENT_KINDS = ["Warranty card", "Receipt / invoice", "Box & papers", "Manual",
                  "Valuation", "Provenance", "Insurance", "Other"]


@dataclass
class ReserveRecord:
    """One power-reserve run: the sample series and the analytics off it."""
    when: str = ""                    # ISO, start of the run
    caliber_key: str = ""
    lift_angle: float = 52.0
    interval_s: int = 300
    stopped_early: bool = False
    hours: float = float("nan")
    samples: List[list] = field(default_factory=list)   # [elapsed_s, rate, amp, beat_error]
    amp_first: float = float("nan")
    amp_last: float = float("nan")
    hours_to_220: float = float("nan")
    hours_to_200: float = float("nan")
    iso_slope: float = float("nan")   # s/day of rate per +1 deg amplitude
    iso_span: float = float("nan")    # rate change over the amplitude range seen
    be_slope: float = float("nan")    # ms beat error per +1 deg amplitude
    notes: str = ""

    @property
    def date(self) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(self.when)
        except (ValueError, TypeError):
            return None


@dataclass
class ServiceRecord:
    """One visit to a watchmaker, with any scanned paperwork attached."""
    when: str = ""                    # ISO date
    kind: str = "Full service"
    performed_by: str = ""            # watchmaker or service centre
    location: str = ""
    cost: str = ""                    # numeric string, or blank
    currency: str = "GBP"
    warranty_months: str = ""
    notes: str = ""
    documents: List[str] = field(default_factory=list)   # filenames inside <root>/docs/
    # Water-resistance test, part of most services.
    wr_result: str = ""               # "", "Pass", "Fail"
    wr_rating: str = ""               # e.g. "100 m / 10 ATM"
    wr_method: str = ""               # "Dry (air pressure)", "Wet", "Condensation", "Vacuum"
    wr_pressure: str = ""             # e.g. "6 bar"

    @property
    def wr_summary(self) -> str:
        if not (self.wr_result or self.wr_rating):
            return ""
        bits = [self.wr_result or "tested"]
        if self.wr_rating:
            bits.append(f"to {self.wr_rating}")
        if self.wr_method:
            bits.append(f"({self.wr_method})")
        return " ".join(bits)

    @property
    def date(self) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(self.when)
        except (ValueError, TypeError):
            return None

    @property
    def cost_value(self) -> Optional[float]:
        try:
            return float(str(self.cost).replace(",", "").replace("£", "")
                         .replace("$", "").replace("€", "").strip())
        except (ValueError, TypeError):
            return None


@dataclass
class Watch:
    id: str = ""
    nickname: str = ""
    brand: str = ""
    model: str = ""
    reference: str = ""
    serial: str = ""
    movement_serial: str = ""
    caliber_key: str = ""
    lift_angle: Optional[float] = None    # override; None uses the caliber value
    material: str = ""
    bezel: str = ""
    crystal: str = ""
    case_size_mm: str = ""
    water_resistance: str = ""
    bracelet: str = ""
    production_year: str = ""
    purchase_date: str = ""
    purchase_price: str = ""
    purchase_currency: str = "GBP"
    purchase_condition: str = ""
    purchased_from: str = ""
    last_service: str = ""
    service_interval_years: str = "5"
    target_rate: str = ""                 # what you are regulating toward, s/day
    photo: str = ""                       # filename inside the photos folder
    notes: str = ""
    history: List[TestRecord] = field(default_factory=list)
    services: List[ServiceRecord] = field(default_factory=list)
    reserves: List[ReserveRecord] = field(default_factory=list)
    # Real-world "on the wrist" rate checks: [{when, set_when, off_seconds, note}].
    # off_seconds is how far ahead (+) the watch read at `when`, having been set
    # to true time at `set_when`.
    wear_checks: List[dict] = field(default_factory=list)
    # Document vault: [{file, kind, name, added, note}] -- warranty cards,
    # receipts, box-and-papers photos, manuals, valuations, provenance.
    documents: List[dict] = field(default_factory=list)
    # Regulation adjustments: [{when, before, after, move, amount, unit, note}].
    # `amount` is signed: positive = toward faster (Avance / weights inward),
    # so (after - before) / amount is s/day gained per unit of that move.
    regulation_log: List[dict] = field(default_factory=list)
    # Free-text tags for filtering ("daily", "safe queen", "for sale", ...).
    tags: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        base = " ".join(x for x in (self.brand, self.model) if x).strip()
        if self.reference:
            base += f" {self.reference}"
        if self.nickname:
            base = f"{self.nickname} -- {base}" if base else self.nickname
        return base or "Untitled watch"

    @property
    def effective_last_service(self) -> str:
        """The newest of the logged service dates and the manual field."""
        dates = [s.when for s in self.services if s.when] + \
                ([self.last_service] if self.last_service else [])
        return max(dates) if dates else ""

    def total_service_cost(self):
        """{currency: total} across logged services with a parseable cost."""
        out = {}
        for s in self.services:
            v = s.cost_value
            if v is not None:
                out[s.currency or "GBP"] = out.get(s.currency or "GBP", 0.0) + v
        return out

    def service_due(self) -> Optional[str]:
        """Plain-language note on service timing, or None if unknown."""
        eff = self.effective_last_service
        if not eff:
            return None
        try:
            last = datetime.fromisoformat(eff)
        except ValueError:
            return None
        try:
            interval = float(self.service_interval_years or 5)
        except ValueError:
            interval = 5.0
        years = (datetime.now() - last).days / 365.25
        if years > interval:
            return (f"Last serviced {years:.1f} years ago, past the "
                    f"{interval:.0f} year interval you set.")
        return (f"Last serviced {years:.1f} years ago, "
                f"{interval - years:.1f} years to go.")


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def _pick(d: dict, cls) -> dict:
    """Only the keys `cls` actually declares -- forward-compatible loading."""
    fields = set(cls.__dataclass_fields__)
    return {k: v for k, v in d.items() if k in fields}


CSV_COLUMNS = [
    "brand", "model", "reference", "nickname", "serial", "movement_serial",
    "caliber_key", "material", "bezel", "crystal", "case_size_mm",
    "water_resistance", "bracelet", "production_year", "purchase_date",
    "purchase_price", "purchase_currency", "purchase_condition", "purchased_from",
    "last_service", "service_interval_years", "target_rate", "tags", "notes",
]

CSV_TEMPLATE = (
    ",".join(CSV_COLUMNS) + "\n"
    "Rolex,Submariner,124060,,,,rolex_3230,Oystersteel,Black ceramic,Sapphire,"
    "41mm,300 m,Oyster,2021,,,GBP,,,,5,+2,\"daily, diver\",No-date Sub\n"
    "Omega,Speedmaster Professional,310.30.42.50.01.001,Moonwatch,,,omega_1861,"
    "Stainless steel,Tachymeter,Hesalite,42mm,50 m,,2022,,,GBP,,,,,,\"chrono\",\n"
)


def import_csv(collection, path):
    """
    Merge watches from a CSV. Header row must name the columns (see
    CSV_COLUMNS); order does not matter and unknown columns are ignored.
    A row with neither brand nor model is skipped. If caliber_key is blank the
    catalogue is asked to resolve it from the reference or brand+model.

    Returns (added, skipped, errors) -- errors is a list of "row N: message".
    """
    added = skipped = 0
    errors = []
    try:
        fh = open(path, newline="", encoding="utf-8-sig")
    except OSError as e:
        return 0, 0, [str(e)]
    with fh:
        rows = list(csv.DictReader(fh))
    field_names = set(Watch.__dataclass_fields__)
    have = {(w.brand.lower().strip(), w.model.lower().strip(),
             w.reference.lower().strip()) for w in collection.watches.values()}
    for i, raw in enumerate(rows, start=2):
        row = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip()
               for k, v in raw.items() if k}
        if not row.get("brand") and not row.get("model"):
            skipped += 1
            continue
        key = (row.get("brand", "").lower(), row.get("model", "").lower(),
               row.get("reference", "").lower())
        if key in have:
            skipped += 1
            continue
        data = {k: v for k, v in row.items() if k in field_names and v != ""}
        data["tags"] = [t.strip() for t in row.get("tags", "").split(",") if t.strip()]
        if not data.get("caliber_key"):
            try:
                from . import catalog
                hit = (catalog.lookup(row.get("reference", ""), row.get("brand", ""))
                       or (catalog.search(f"{row.get('brand','')} {row.get('model','')}")
                           or [None])[0])
                if hit and getattr(hit, "caliber_key", ""):
                    data["caliber_key"] = hit.caliber_key
            except Exception:
                pass
        try:
            w = Watch(**data)
        except TypeError as e:
            errors.append(f"row {i}: {e}")
            continue
        w.id = uuid.uuid4().hex[:12]
        collection.watches[w.id] = w
        have.add(key)
        added += 1
    if added:
        collection.save()
    return added, skipped, errors


def _caliber_text(w) -> str:
    try:
        from .calibers import CALIBERS
        c = CALIBERS.get(w.caliber_key)
        return f"{w.model} {c.name if c else ''} {w.caliber_key}".lower()
    except Exception:
        return f"{w.model} {w.caliber_key}".lower()


# Computed collections -- name -> predicate(watch) -> bool.
SMART_COLLECTIONS = {
    "Needs service": lambda w: bool(w.service_due() and "past the" in (w.service_due() or "")),
    "Never measured": lambda w: not w.history,
    "Not measured in 6 months": lambda w: bool(
        w.history and (datetime.now() - max(
            (h.date for h in w.history if h.date), default=datetime.now())).days > 182),
    "Divers": lambda w: any(k in f"{w.model} {w.water_resistance}".lower() for k in (
        "sub", "sea-dweller", "diver", "pelagos", "seamaster", "fifty fathoms",
        "aquaracer", "turtle", "300m", "200m", "600m", "1000m")),
    "Chronographs": lambda w: any(k in _caliber_text(w) for k in (
        "chrono", "daytona", "speedmaster", "7750", "el primero", "b01", "valjoux")),
    "Has open reminders": lambda w: False,   # filled in by reminders(), see below
}


def reminders(collection, now=None):
    """
    Actionable things across the collection: service overdue, warranty about to
    lapse, watches gone quiet. Returns [{watch_id, kind, text, severity}].
    severity is "warn" or "info".
    """
    now = now or datetime.now()
    out = []
    for w in collection.watches.values():
        due = w.service_due()
        if due and "past the" in due:
            out.append({"watch_id": w.id, "kind": "service", "severity": "warn",
                        "text": f"{w.label}: {due}"})
        for s in w.services:
            try:
                months = int(float(s.warranty_months))
                start = datetime.fromisoformat(s.when)
            except (ValueError, TypeError):
                continue
            if months <= 0:
                continue
            expiry = start + timedelta(days=months * 30)
            days = (expiry - now).days
            if 0 <= days <= 60:
                out.append({"watch_id": w.id, "kind": "warranty", "severity": "warn",
                            "text": f"{w.label}: service warranty ends in {days} days "
                                    f"({expiry:%d %b %Y})"})
        if w.history:
            last = max((h.date for h in w.history if h.date), default=None)
            if last and (now - last).days > 182:
                out.append({"watch_id": w.id, "kind": "quiet", "severity": "info",
                            "text": f"{w.label}: not measured for "
                                    f"{(now - last).days // 30} months"})
    return out


def smart_match(watch, key, open_ids=frozenset()):
    if key == "Has open reminders":
        return watch.id in open_ids
    pred = SMART_COLLECTIONS.get(key)
    try:
        return bool(pred(watch)) if pred else True
    except Exception:
        return False


def regulation_sensitivity(watch):
    """
    Learn this watch's index sensitivity from its regulation log.

    Returns {unit, spd_per_unit, n, scatter} for the most-used unit, or None.
    `spd_per_unit` is s/day gained per +1 unit of adjustment toward faster.
    """
    by_unit = {}
    for e in getattr(watch, "regulation_log", []):
        try:
            amt = float(e.get("amount", 0) or 0)
            before = float(e["before"])
            after = float(e["after"])
        except (KeyError, TypeError, ValueError):
            continue
        unit = (e.get("unit") or "").strip().lower()
        if not unit or abs(amt) < 1e-6:
            continue
        by_unit.setdefault(unit, []).append((after - before) / amt)
    if not by_unit:
        return None
    unit, vals = max(by_unit.items(), key=lambda kv: len(kv[1]))
    if len(vals) < 1:
        return None
    v = np.array(vals, dtype=float)
    return {"unit": unit, "spd_per_unit": float(np.median(v)),
            "n": int(v.size),
            "scatter": float(np.std(v)) if v.size > 1 else 0.0}


def wear_rate_series(watch):
    """
    (datetime, rate_spd) per wrist-rate check: the watch's real rate on the
    wrist, off_seconds spread over the days since it was set to true time.
    """
    out = []
    for c in sorted(watch.wear_checks, key=lambda x: x.get("when", "")):
        try:
            when = datetime.fromisoformat(c["when"])
            set_when = datetime.fromisoformat(c["set_when"])
        except (ValueError, KeyError, TypeError):
            continue
        days = (when - set_when).total_seconds() / 86400.0
        if days <= 0.02:
            continue
        out.append((when, float(c.get("off_seconds", 0.0)) / days))
    return out


class Collection:
    def __init__(self, root: str):
        self.root = root
        self.path = os.path.join(root, "collection.json")
        self.photos = os.path.join(root, "photos")
        self.docs = os.path.join(root, "docs")
        for d in (self.photos, self.docs):
            os.makedirs(d, exist_ok=True)
        self.watches: Dict[str, Watch] = {}
        self.load()

    def load(self):
        self.watches = {}
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        for d in raw.get("watches", []):
            hist = [TestRecord(**_pick(h, TestRecord)) for h in d.pop("history", [])]
            svcs = [ServiceRecord(**_pick(s, ServiceRecord)) for s in d.pop("services", [])]
            rsvs = [ReserveRecord(**_pick(r, ReserveRecord)) for r in d.pop("reserves", [])]
            known = {f for f in Watch.__dataclass_fields__}
            w = Watch(**{k: v for k, v in d.items() if k in known})
            w.history = hist
            w.services = svcs
            w.reserves = rsvs
            self.watches[w.id] = w

    def save(self):
        tmp = self.path + ".tmp"
        data = {"version": 1, "saved": datetime.now().isoformat(timespec="seconds"),
                "watches": []}
        for w in self.watches.values():
            d = asdict(w)
            d["history"] = [asdict(h) for h in w.history]
            d["services"] = [asdict(s) for s in w.services]
            d["reserves"] = [asdict(r) for r in w.reserves]
            data["watches"].append(d)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        # Write to a temp file and move it into place, so an interrupted save
        # cannot leave you with a half-written collection.
        os.replace(tmp, self.path)

    def add(self, w: Watch) -> Watch:
        if not w.id:
            w.id = uuid.uuid4().hex[:12]
        self.watches[w.id] = w
        self.save()
        return w

    def remove(self, watch_id: str):
        w = self.watches.pop(watch_id, None)
        if w and w.photo:
            try:
                os.remove(os.path.join(self.photos, w.photo))
            except OSError:
                pass
        if w:
            docs = [d for s in w.services for d in s.documents] + \
                   [d.get("file", "") for d in w.documents]
            for doc in docs:
                if not doc:
                    continue
                try:
                    os.remove(os.path.join(self.docs, doc))
                except OSError:
                    pass
        self.save()

    def sorted_watches(self) -> List[Watch]:
        return sorted(self.watches.values(), key=lambda w: w.label.lower())

    def store_photo(self, watch_id: str, src: str) -> str:
        """Copy a photo into the collection folder; returns the stored filename."""
        ext = os.path.splitext(src)[1].lower() or ".jpg"
        name = f"{watch_id}{ext}"
        shutil.copyfile(src, os.path.join(self.photos, name))
        return name

    def photo_path(self, w: Watch) -> Optional[str]:
        if not w.photo:
            return None
        p = os.path.join(self.photos, w.photo)
        return p if os.path.exists(p) else None

    def store_document(self, watch_id: str, src: str) -> str:
        """Copy a service document into the collection; returns the stored filename."""
        ext = os.path.splitext(src)[1].lower() or ".pdf"
        name = f"{watch_id}_{uuid.uuid4().hex[:8]}{ext}"
        shutil.copyfile(src, os.path.join(self.docs, name))
        return name

    def document_path(self, name: str) -> Optional[str]:
        if not name:
            return None
        p = os.path.join(self.docs, name)
        return p if os.path.exists(p) else None


# --------------------------------------------------------------------------
# Building a record from a session
# --------------------------------------------------------------------------

def record_from_readings(readings, caliber_key: str, lift_angle: float,
                         notes: str = "", service_event: bool = False) -> TestRecord:
    rec = TestRecord(when=datetime.now().isoformat(timespec="seconds"),
                     caliber_key=caliber_key, lift_angle=float(lift_angle),
                     notes=notes, service_event=service_event)
    rec.readings = [{"position": r.position, "wind": r.wind_state, "rate": r.rate,
                     "amplitude": r.amplitude, "beat_error": r.beat_error}
                    for r in readings]
    rates = [r.rate for r in readings if r.rate == r.rate]
    amps = [r.amplitude for r in readings if r.amplitude == r.amplitude]
    bes = [r.beat_error for r in readings if r.beat_error == r.beat_error]
    if rates:
        rec.mean_rate = float(np.mean(rates))
        rec.delta_rate = float(max(rates) - min(rates))
    if amps:
        rec.max_amplitude = float(max(amps))
        rec.min_amplitude = float(min(amps))
    if bes:
        rec.max_beat_error = float(max(bes))
    return rec


# --------------------------------------------------------------------------
# Trends
# --------------------------------------------------------------------------

@dataclass
class Trend:
    metric: str
    unit: str
    n: int = 0
    first: float = float("nan")
    last: float = float("nan")
    mean: float = float("nan")
    stdev: float = float("nan")
    span_days: float = 0.0
    per_year: float = float("nan")     # least-squares slope, units per year
    verdict: str = ""


def _series(history: List[TestRecord], attr: str):
    xs, ys = [], []
    for h in sorted(history, key=lambda h: h.when):
        d = h.date
        v = getattr(h, attr, float("nan"))
        if d is None or v != v:
            continue
        xs.append(d.timestamp())
        ys.append(float(v))
    return np.array(xs), np.array(ys)


def trend(history: List[TestRecord], attr: str, metric: str, unit: str,
          worse_when_falling: bool = False, noise: float = 0.0) -> Trend:
    """
    Least-squares slope of one metric across the history, expressed per year.

    `noise` is the run-to-run repeatability of the measurement. A slope
    smaller than that is not evidence of anything, and saying so plainly
    matters more than producing a number -- three measurements on a hobby
    timegrapher will always have SOME slope.
    """
    t = Trend(metric=metric, unit=unit)
    xs, ys = _series(history, attr)
    t.n = int(ys.size)
    if ys.size == 0:
        t.verdict = "No data yet."
        return t
    t.first, t.last = float(ys[0]), float(ys[-1])
    t.mean, t.stdev = float(np.mean(ys)), float(np.std(ys, ddof=1) if ys.size > 1 else 0.0)
    if ys.size < 3:
        t.verdict = f"{ys.size} measurement(s). Three or more are needed for a trend."
        return t

    t.span_days = float((xs[-1] - xs[0]) / 86400.0)
    if t.span_days < 1:
        t.verdict = "All measurements on the same day -- no trend over time."
        return t

    years = (xs - xs[0]) / (365.25 * 86400.0)
    A = np.vstack([np.ones_like(years), years]).T
    sol, *_ = np.linalg.lstsq(A, ys, rcond=None)
    t.per_year = float(sol[1])

    change = abs(t.per_year)
    if change < max(noise, 1e-9):
        t.verdict = (f"Stable. The {change:.1f} {unit}/year slope is smaller than the "
                     f"run-to-run repeatability of the measurement itself, so it is "
                     f"not evidence of a real change.")
    else:
        direction = "falling" if t.per_year < 0 else "rising"
        bad = (t.per_year < 0) if worse_when_falling else (t.per_year > 0)
        t.verdict = (f"{metric} is {direction} by {change:.1f} {unit} per year across "
                     f"{t.span_days/365.25:.1f} years"
                     + (". Worth watching." if bad else ". Not a concern in itself."))
    return t


def summarise(w: Watch) -> List[Trend]:
    """The four trends worth tracking, with sensible noise floors."""
    h = w.history
    return [
        trend(h, "mean_rate", "Mean rate", "s/day", noise=1.5),
        trend(h, "max_amplitude", "Peak amplitude", "deg",
              worse_when_falling=True, noise=8.0),
        trend(h, "delta_rate", "Positional delta", "s/day", noise=3.0),
        trend(h, "max_beat_error", "Beat error", "ms", noise=0.15),
    ]


def health_notes(w: Watch) -> List[str]:
    """Plain-language observations across the history."""
    out = []
    if len(w.history) < 2:
        return out
    hist = sorted(w.history, key=lambda h: h.when)
    amps = [h.max_amplitude for h in hist if h.max_amplitude == h.max_amplitude]
    if len(amps) >= 3:
        drop = amps[0] - amps[-1]
        if drop > 30:
            out.append(
                f"Peak amplitude is down {drop:.0f} degrees since the first recorded "
                f"test. A steady decline usually means the lubricants are ageing "
                f"rather than anything sudden -- this is what a service interval is "
                f"actually for.")
    svc = [h for h in w.history if h.service_event]
    if svc:
        out.append(f"{len(svc)} run(s) marked as post-service. Comparing the runs "
                   f"either side of one is the clearest read on whether the service "
                   f"achieved anything.")
    due = w.service_due()
    if due:
        out.append(due)
    return out
