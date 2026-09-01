"""
The single watch catalog.

Two datasets grew up separately: `models` maps model names to movements across
generations, `references` carries per-reference detail like case metal, bezel
and nickname. Having the model finder read one and the watch profile editor
read the other meant a watch you could look up was not necessarily a watch you
could save, which is exactly the kind of split that makes a tool feel broken.

This merges them into one list. A reference entry wins over a model entry for
the same watch, since it carries strictly more information.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import models as modeldb
from . import references as refdb


@dataclass
class WatchEntry:
    brand: str
    model: str
    reference: str = ""
    years: str = ""
    caliber_key: str = ""
    material: str = ""
    bezel: str = ""
    crystal: str = ""
    nickname: str = ""
    notes: str = ""
    confidence: str = "sure"
    has_detail: bool = False       # True when it came from the reference table

    @property
    def label(self) -> str:
        s = f"{self.brand} {self.model}".strip()
        if self.reference:
            s += f" {self.reference}"
        return s


def _norm(t: str) -> str:
    return "".join(ch for ch in t.lower() if ch.isalnum())


def _build() -> List[WatchEntry]:
    out: List[WatchEntry] = []
    seen = set()

    for r in refdb.REFERENCES:
        key = (_norm(r.brand), _norm(r.model), _norm(r.reference))
        if key in seen:
            continue
        seen.add(key)
        out.append(WatchEntry(
            brand=r.brand, model=r.model, reference=r.reference, years=r.years,
            caliber_key=r.caliber_key, material=r.material, bezel=r.bezel,
            crystal=r.crystal, nickname=r.nickname, notes=r.notes,
            confidence="check" if "verify" in (r.notes or "").lower() else "sure",
            has_detail=True))

    # Model-level entries fill the gaps: generations catalogued by variant text
    # rather than by a reference number.
    for m in modeldb.MODELS:
        key = (_norm(m.brand), _norm(m.model), _norm(m.variant))
        if key in seen:
            continue
        # Skip if a reference entry already covers this brand/model/caliber.
        if any(e.brand == m.brand and _norm(e.model) == _norm(m.model)
               and e.caliber_key == m.caliber_key for e in out):
            continue
        seen.add(key)
        out.append(WatchEntry(
            brand=m.brand, model=m.model, reference=m.variant, years=m.years,
            caliber_key=m.caliber_key, confidence=m.confidence, has_detail=False))

    out.sort(key=lambda e: (e.brand, e.model, e.years))
    return out


CATALOG: List[WatchEntry] = _build()


def search(query: str, known_calibers=None) -> List[WatchEntry]:
    """
    Find watches by brand, model, reference or nickname.

    Entries whose movement is not in the caliber database are dropped -- a
    result you cannot act on is worse than no result.
    """
    if known_calibers is None:
        from .calibers import CALIBERS
        known_calibers = CALIBERS
    q = _norm(query)
    rows = CATALOG if not q else [
        e for e in CATALOG
        if q in _norm(e.brand + e.model + e.reference + e.nickname + e.years)]
    rows = [e for e in rows if e.caliber_key in known_calibers]
    # Exact model-name hits first, then richer entries, then chronological.
    rows.sort(key=lambda e: (0 if q and q in _norm(e.model) else 1,
                             not e.has_detail, e.brand, e.model, e.years))
    return rows


def brands() -> List[str]:
    return sorted({e.brand for e in CATALOG})


def models_for(brand: str) -> List[str]:
    qb = _norm(brand)
    return sorted({e.model for e in CATALOG if _norm(e.brand) == qb})


def references_for(brand: str, model: str) -> List[WatchEntry]:
    qb, qm = _norm(brand), _norm(model)
    out = [e for e in CATALOG
           if _norm(e.brand) == qb and (qm in _norm(e.model) or _norm(e.model) in qm)
           and e.reference]
    out.sort(key=lambda e: e.years, reverse=True)
    return out


def lookup(reference: str, brand: str = "") -> Optional[WatchEntry]:
    """Exact reference lookup, falling back to the Rolex decoder."""
    q = _norm(reference)
    if not q:
        return None
    qb = _norm(brand)
    for want_brand in ((qb,) if qb else ()) + ("",):
        for e in CATALOG:
            if _norm(e.reference) == q and (not want_brand or _norm(e.brand) == want_brand):
                return e
    for e in CATALOG:
        if e.reference and q in _norm(e.reference):
            return e
    if not brand or qb == "rolex":
        r = refdb.decode_rolex(reference)
        if r:
            return WatchEntry(brand="Rolex", model=r.model, reference=r.reference,
                              caliber_key="", material=r.material, bezel=r.bezel,
                              crystal=r.crystal, notes=r.notes,
                              confidence="check", has_detail=True)
    return None


def stats():
    from .calibers import CALIBERS
    usable = [e for e in CATALOG if e.caliber_key in CALIBERS]
    return {"entries": len(CATALOG), "usable": len(usable),
            "brands": len({e.brand for e in usable}),
            "models": len({(e.brand, e.model) for e in usable})}
