"""
Master lists used by the respondent form (requirements points 4 and 5):
  occupation       Occupation master
  sec_class        Class master (socio-economic classification: SEC A, B, C ...)
  respondent_type  End consumer / Retailer / ...
Admins manage them (api/masters.py); survey persons pick from them.
"""

from app.database.database import SessionLocal
from app.database.models import MasterValue

KINDS = {"occupation": "Occupation", "sec_class": "Class (SEC)", "respondent_type": "Respondent type"}

DEFAULTS = {
    "occupation": ["Student", "Homemaker", "Salaried", "Self-employed", "Business owner", "Farmer", "Retired", "Other"],
    "sec_class": ["SEC A", "SEC B", "SEC C", "SEC D", "SEC E"],
    "respondent_type": ["End consumer", "Retailer", "Distributor", "Wholesaler", "Other"],
}


def seed_defaults() -> None:
    """Fill an empty list with its defaults (once; admins can change them afterwards)."""
    db = SessionLocal()
    try:
        for kind, names in DEFAULTS.items():
            if db.query(MasterValue).filter(MasterValue.kind == kind).count():
                continue
            for i, name in enumerate(names):
                db.add(MasterValue(kind=kind, name=name, position=i))
        db.commit()
    finally:
        db.close()


def active_names(db, kind: str) -> set:
    return {n.lower() for (n,) in db.query(MasterValue.name).filter(MasterValue.kind == kind, MasterValue.is_active == 1).all()}
