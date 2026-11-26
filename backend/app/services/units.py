"""
Units of measure, from Other Master Data only: m_othermasterdata values of MEASUREMENT_UNIT,
each with its conversion factor in extra.to_base (in its category's base: kg for Weight/Mass,
L for Volume; 1 L is counted as 1 kg for batch weights). Nothing here lists units or factors:
a unit added, changed or made inactive in Other Master works everywhere (recipes, batch weight,
costing, recipe standards) without a code change.

TO_KG is a read-only mapping {lower-case unit or spelling: kg per unit}, reloaded from the
database at most every CACHE_SECONDS and at once after a change (invalidate()).
SPELLINGS are other ways people write a master unit ("kgs", "ltr", "ton"): each points to a
master unit code and takes that unit's factor, so a spelling disappears with its unit.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Optional

CACHE_SECONDS = 60
MASTER = "MEASUREMENT_UNIT"

# Other ways of writing a master unit -> that unit's code (not units of their own: no factors).
SPELLINGS = {
    "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "milligram": "mg", "milligrams": "mg", "ug": "µg", "μg": "µg", "microgram": "µg", "micrograms": "µg",
    "t": "MT", "ton": "MT", "tons": "MT", "tonne": "MT", "tonnes": "MT",
    "ltr": "L", "ltrs": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L",
    "millilitre": "mL", "milliliter": "mL", "ul": "µL", "μl": "µL", "kilolitre": "kL", "kiloliter": "kL",
}


def _from_db() -> dict:
    """{code: (to_base, base)} of the active units in Other Master (empty if there are none)."""
    from app.database.database import SessionLocal
    from app.database.models import OtherMasterData, OtherMasterName

    db = SessionLocal()
    try:
        master = db.query(OtherMasterName).filter(OtherMasterName.master_code == MASTER).first()
        if master is None or not master.is_active:
            return {}
        out = {}
        for v in db.query(OtherMasterData).filter(OtherMasterData.master_id == master.id, OtherMasterData.is_active == 1):
            extra = v.extra or {}
            try:
                factor = float(extra.get("to_base"))
            except (TypeError, ValueError):
                continue
            if factor > 0:
                out[v.data_code] = (factor, extra.get("base") or "kg")
        return out
    except Exception:  # noqa: BLE001 - no database (tests of pure functions): the seed defaults below
        return {}
    finally:
        db.close()


def _seed_defaults() -> dict:
    """Only when the database has no units yet (a fresh install before its first start): the
    starting rows Other Master is seeded with (services/lov.py), never a second list to maintain."""
    from app.services.lov import UNIT_CATEGORIES, UNITS

    bases = {code: base for code, _, base in UNIT_CATEGORIES}
    return {code: (factor, bases[cat]) for cat, units in UNITS.items() for code, _, factor in units}


class _UnitTable(Mapping):
    def __init__(self):
        self._lock = threading.Lock()
        self._table: Optional[dict] = None
        self._units: dict = {}
        self._loaded_at = 0.0

    def invalidate(self) -> None:
        with self._lock:
            self._table = None

    def _load(self) -> dict:
        with self._lock:
            if self._table is not None and time.monotonic() - self._loaded_at < CACHE_SECONDS:
                return self._table
            units = _from_db() or _seed_defaults()
            table = {code.lower(): factor for code, (factor, _) in units.items()}
            by_code = {code.lower(): code for code in units}
            for spelling, code in SPELLINGS.items():
                if code.lower() in by_code and spelling not in table:
                    table[spelling] = table[code.lower()]
            self._units, self._table, self._loaded_at = units, table, time.monotonic()
            return table

    def __getitem__(self, key):
        return self._load()[key]

    def __iter__(self):
        return iter(self._load())

    def __len__(self):
        return len(self._load())

    def units(self) -> dict:
        """{code: (to_base, base)} as in Other Master."""
        self._load()
        return dict(self._units)


TO_KG = _UnitTable()


def invalidate() -> None:
    TO_KG.invalidate()
