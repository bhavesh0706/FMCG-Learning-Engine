"""
LOV (list of values) kept in m_othermastername (the lists) and m_othermasterdata
(their values). The web app reads them from GET /lov; admins change them in
Catalog > Master lists (api/lov.py).

DEFAULTS below is only the starting data: seed_lov() adds lists and values that
are missing and never overwrites what an admin changed.

is_system lists: the program uses their codes (to colour a sentiment, to know a
question type ...), so only labels and order may change. Open lists (is_system 0)
can also get new values and hide old ones.
"""

from app.database.database import SessionLocal
from app.database.models import OtherMasterData, OtherMasterName

# code, name, is_system, [(data_code, data_value, extra), ...]
DEFAULTS = [
    ("USAGE_PERIOD", "Usage period", 0, [
        ("last_week", "Less than 7 days", None),
        ("last_month", "For the past 1 month", None),
        ("last_3_months", "For the last 3 months", None),
        ("more_than_3_months", "For more than 3 months", None),
    ]),
    ("GENDER", "Gender", 0, [
        ("Male", "Male", None),
        ("Female", "Female", None),
        ("Other", "Other", None),
        ("Prefer not to say", "Prefer not to say", None),
    ]),
    ("REPORT_BREAKDOWN", "Report breakdowns", 1, [
        ("gender", "Gender", None),
        ("age_group", "Age group", None),
        ("city", "City", None),
        ("usage_period", "How long used", None),
        ("respondent_type", "Respondent type", None),
        ("audience", "Internal / External", None),
        ("country", "Country", None),
        ("occupation", "Occupation", None),
        ("sec_class", "Class (SEC)", None),
        ("state", "State", None),
    ]),
    ("SENTIMENT", "Sentiment", 1, [
        ("positive", "Positive", {"tone": "positive"}),
        ("concerns", "Positive, with concerns", {"tone": "mixed"}),
        ("mixed", "Mixed", {"tone": "mixed"}),
        ("negative", "Negative", {"tone": "negative"}),
        ("neutral", "Neutral", {"tone": "neutral"}),
    ]),
    ("SEVERITY", "Severity", 1, [
        ("high", "High", None),
        ("medium", "Medium", None),
        ("low", "Low", None),
    ]),
    ("QUESTION_TYPE", "Question type", 1, [
        ("rating", "Rating 1–5", None),
        ("yes_no", "Yes / No", None),
        ("single_choice", "Choice", None),
        ("multiple_choice", "Multiple choice", None),
        ("preference", "Preference (Sample A vs B)", None),
        ("text", "Text answer", None),
    ]),
    ("REPORT_ROLE", "Question report role", 1, [
        ("preference", "Product preference", {"hint": "This product vs the alternative. Put this product as the FIRST option."}),
        ("price", "Price willingness", {"hint": "Price points as options, e.g. 3 ghc, 4 ghc, 5 ghc."}),
        ("score", "Overall score", {"hint": "1–5 rating, or options from lowest to highest, e.g. 7 marks … 10 marks."}),
        ("improvement", "Improvement areas", {"hint": "What customers would change."}),
        ("purchase_intent", "Purchase intent", {"hint": "Would they buy it? Put the positive answer first."}),
        ("ease", "Ease of use / preparation", {"hint": "How easy it was to use or prepare."}),
    ]),
    ("USER_ROLE", "User role", 1, [
        ("admin", "Admin", None),
        ("executive", "Survey person", None),
        ("client", "Super Admin", None),
    ]),
    ("PRODUCT_STATE", "Product state", 1, [
        ("feedback", "Has feedback", {"tone": "ok"}),
        ("new", "No feedback yet", {"tone": "neutral"}),
        ("setup", "Needs questions", {"tone": "warn"}),
    ]),
    ("SURVEY_STATUS", "Survey status", 1, [
        ("needs_review", "Product to verify", {"tone": "warn"}),
        ("invalid", "Marked invalid", {"tone": "neg"}),
        ("analyzed", "Analyzed", {"tone": "ok"}),
        ("processing", "Analyzing…", {"tone": "act"}),
        ("queued", "In queue", {"tone": "neutral"}),
        ("failed", "Analysis failed", {"tone": "warn"}),
        ("none", "No recording yet", {"tone": "neutral"}),
    ]),
    ("RATING_SCALE", "Rating scale", 1, [
        ("5", "5 ★", None),
        ("4", "4 ★", None),
        ("3", "3 ★", None),
        ("2", "2 ★", None),
        ("1", "1 ★", None),
    ]),
]


# Default labels that were renamed later: (list, code): (old default, new default).
# A value still showing the old default is updated; a label an admin changed is kept.
RENAMED_DEFAULTS = {
    ("SURVEY_STATUS", "needs_review"): ("Product check", "Product to verify"),  # Survey history tab renamed
    # "How long have you been consuming / using ...?" (was "When did they use ...?")
    ("USAGE_PERIOD", "last_week"): ("In the last week", "Less than 7 days"),
    ("USAGE_PERIOD", "last_month"): ("In the last month", "For the past 1 month"),
    ("USAGE_PERIOD", "last_3_months"): ("In the last 3 months", "For the last 3 months"),
    ("USAGE_PERIOD", "more_than_3_months"): ("More than 3 months ago", "For more than 3 months"),
    ("REPORT_BREAKDOWN", "usage_period"): ("When used", "How long used"),
}


def seed_lov() -> int:
    """Add missing lists and values (start-up). Returns how many rows were added."""
    added = 0
    db = SessionLocal()
    try:
        for (code, data_code), (old_label, new_label) in RENAMED_DEFAULTS.items():
            master = db.query(OtherMasterName).filter(OtherMasterName.master_code == code).first()
            if master is not None:
                row = (db.query(OtherMasterData)
                       .filter(OtherMasterData.master_id == master.id, OtherMasterData.data_code == data_code).first())
                if row is not None and row.data_value == old_label:
                    row.data_value = new_label
                    print(f"Database updated: {code} / {data_code} label is now “{new_label}”")
        for code, name, is_system, values in DEFAULTS:
            master = db.query(OtherMasterName).filter(OtherMasterName.master_code == code).first()
            if master is None:
                master = OtherMasterName(master_code=code, master_name=name, is_system=is_system)
                db.add(master)
                db.flush()
                added += 1
            have = {c for (c,) in db.query(OtherMasterData.data_code).filter(OtherMasterData.master_id == master.id)}
            for position, (data_code, label, extra) in enumerate(values):
                if data_code not in have:
                    db.add(OtherMasterData(master_id=master.id, data_code=data_code, data_value=label,
                                           sort_order=position, extra=extra))
                    added += 1
        added += _seed_geo(db)
        db.commit()
    finally:
        db.close()
    if added:
        print(f"Database updated: {added} LOV rows added (m_othermastername / m_othermasterdata)")
    return added


# Country -> State -> City (open lists: admins add more). Data in services/lov_geo.py.
GEO_LISTS = [("COUNTRY", "Country", None), ("STATE", "State", "COUNTRY"), ("CITY", "City", "STATE")]


def _seed_geo(db) -> int:
    from app.services.lov_geo import COUNTRIES, INDIA, city_entries

    added = 0
    masters = {}
    for code, name, parent_code in GEO_LISTS:
        master = db.query(OtherMasterName).filter(OtherMasterName.master_code == code).first()
        if master is None:
            master = OtherMasterName(master_code=code, master_name=name, is_system=0)
            db.add(master)
            added += 1
        if parent_code and master.parent_master_id is None:
            master.parent_master_id = masters[parent_code].id
        db.flush()
        masters[code] = master

    existing = {(r.master_id, r.data_code): r for r in db.query(OtherMasterData)
                .filter(OtherMasterData.master_id.in_([m.id for m in masters.values()]))}

    def ensure(master, data_code, label, aliases, parent, position):
        nonlocal added
        row = existing.get((master.id, data_code))
        if row is None:
            row = OtherMasterData(master_id=master.id, data_code=data_code, data_value=label, sort_order=position,
                                  parent_id=parent.id if parent else None, extra={"aliases": aliases} if aliases else None)
            db.add(row)
            db.flush()
            added += 1
        elif aliases:
            # Aliases added to the starting data later reach existing rows too (labels stay as they are).
            have = list((row.extra or {}).get("aliases") or [])
            missing = [a for a in aliases if a not in have]
            if missing:
                row.extra = {**(row.extra or {}), "aliases": have + missing}
                added += 1
        return row

    for i, (code, name, aliases) in enumerate(COUNTRIES):
        country = ensure(masters["COUNTRY"], code, name, aliases, None, i)
        if code != "IN":
            continue
        for j, (state_code, state_name, state_aliases, cities) in enumerate(sorted(INDIA, key=lambda s: s[1])):
            state = ensure(masters["STATE"], state_code, state_name, state_aliases, country, j)
            for k, (city, city_aliases) in enumerate(sorted(city_entries(cities))):
                ensure(masters["CITY"], f"{state_code}/{city}", city, city_aliases, state, k)
    return added