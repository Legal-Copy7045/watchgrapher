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

import json
import os
import shutil
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
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

    @property
    def date(self) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(self.when)
        except (ValueError, TypeError):
            return None

    @property
    def cost_value(self) -> Optional[float]:
        try:
            return float(str(self.cost).replace(",", "").replace("£", "").replace("$", "").strip())
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
            known = {f for f in Watch.__dataclass_fields__}
            w = Watch(**{k: v for k, v in d.items() if k in known})
            w.history = hist
            w.services = svcs
            self.watches[w.id] = w

    def save(self):
        tmp = self.path + ".tmp"
        data = {"version": 1, "saved": datetime.now().isoformat(timespec="seconds"),
                "watches": []}
        for w in self.watches.values():
            d = asdict(w)
            d["history"] = [asdict(h) for h in w.history]
            d["services"] = [asdict(s) for s in w.services]
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
            for s in w.services:
                for doc in s.documents:
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
    amps = [h.max_amplitude for h in w.history if h.max_amplitude == h.max_amplitude]
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
