"""
Convert stored file paths to storage keys (services/storage.py), safely.

Older rows hold absolute paths from the computer that saved them, for example
    C:\\Users\\itone\\FMCG_Learning_Engine\\uploads\\videos\\123_x.mp4
They already work everywhere (the storage service finds the file by its folder and
name), so converting is optional: it makes the database computer-independent.

    python -m app.tools.storage_keys            # dry run: report only, change nothing
    python -m app.tools.storage_keys --apply    # convert the rows whose file is found here

Only rows whose file is FOUND on this computer are converted; every old value is
saved to a backup CSV first (backend/uploads/storage_keys_backup_<time>.csv, or
--backup PATH). Rows whose file isn't here are left exactly as they are. No file
is moved, copied or deleted.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from app.database.database import SessionLocal
from app.database.models import AnalysisJob, FeedbackRecord, RecipeAttachment
from app.services.storage import BACKEND_DIR, get_storage

TABLES = [
    ("feedback_records", FeedbackRecord, "video_path"),
    ("analysis_jobs", AnalysisJob, "stored_path"),
    ("recipe_attachments", RecipeAttachment, "stored_path"),
]


def plan(db) -> dict:
    """{table: {"keys": n, "convert": [(id, old, new)], "missing": [(id, old)]}}"""
    st = get_storage()
    out = {}
    for table, model, column in TABLES:
        info = {"keys": 0, "convert": [], "missing": []}
        for row in db.query(model).filter(getattr(model, column).isnot(None)).order_by(model.id):
            old = getattr(row, column)
            if st.path_for(old) is not None:
                info["keys"] += 1
                continue
            found = st.resolve(old)
            new = st.key_for(found) if found else None
            if new:
                info["convert"].append((row.id, old, new))
            else:
                info["missing"].append((row.id, old))
        out[table] = info
    return out


def apply(db, changes: dict, backup: Path) -> int:
    rows = [(t, i, old, new) for t, info in changes.items() for i, old, new in info["convert"]]
    if not rows:
        return 0
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table", "id", "old_value", "new_key"])
        w.writerows(rows)
    models = {t: (m, c) for t, m, c in TABLES}
    for table, row_id, old, new in rows:
        model, column = models[table]
        row = db.get(model, row_id)
        if row is not None and getattr(row, column) == old:  # unchanged since the plan
            setattr(row, column, new)
    db.commit()
    return len(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Convert stored file paths to storage keys (dry run unless --apply).")
    parser.add_argument("--apply", action="store_true", help="convert the rows whose file is found on this computer")
    parser.add_argument("--backup", help="where to save the old values (CSV)")
    args = parser.parse_args(argv)
    st = get_storage()
    print("Storage folders on this computer:")
    for area, folder in st.areas.items():
        print(f"  {area:13} {folder}")
    db = SessionLocal()
    try:
        changes = plan(db)
        for table, info in changes.items():
            print(f"\n{table}: {info['keys']} already keys, {len(info['convert'])} can be converted, "
                  f"{len(info['missing'])} not found here (left unchanged)")
            for row_id, old, new in info["convert"][:5]:
                print(f"  #{row_id}: {old}  ->  {new}")
            for row_id, old in info["missing"][:5]:
                print(f"  #{row_id} not found: {old}")
        if not args.apply:
            print("\nDry run: nothing changed. Run with --apply to convert the rows listed above.")
            return 0
        backup = Path(args.backup) if args.backup else BACKEND_DIR / "uploads" / f"storage_keys_backup_{datetime.now():%Y%m%d_%H%M%S}.csv"
        n = apply(db, changes, backup)
        print(f"\nConverted {n} rows. Old values saved to {backup}." if n else "\nNothing to convert.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
