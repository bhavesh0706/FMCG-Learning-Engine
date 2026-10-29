"""
Read-only check: can this computer play every stored recording?

Run from the backend folder (virtual environment active):
    python -m app.tools.check_recordings

For each feedback record with a recording it reports one of:
  OK        the file is at the stored path
  FOUND     the stored path is from another computer (for example a Windows path
            after moving to a Mac) and the file was found in this computer's
            uploads folder; it plays (app.services.recordings)
  MISSING   the file isn't on this computer: copy the "uploads" folder from the
            computer that received it into this project folder
Nothing is changed and no secrets are printed.
"""

from __future__ import annotations

from pathlib import Path

from app.database.database import SessionLocal
from app.database.models import FeedbackRecord
from app.services.recordings import resolve_recording, uploads_roots


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.query(FeedbackRecord).filter(FeedbackRecord.video_path.isnot(None)).order_by(FeedbackRecord.id).all()
        ok, found, missing = [], [], []
        for rec in rows:
            if not rec.video_path:
                continue
            if Path(rec.video_path).is_file():
                ok.append(rec)
            elif resolve_recording(rec.video_path) is not None:
                found.append(rec)
            else:
                missing.append(rec)
        print(f"Recordings: {len(ok) + len(found) + len(missing)}")
        print(f"  OK       {len(ok):4}  at the stored path")
        print(f"  FOUND    {len(found):4}  stored path from another computer; found in this uploads folder (plays)")
        print(f"  MISSING  {len(missing):4}  not on this computer")
        print("Uploads folder(s) searched: " + ", ".join(str(r) for r in uploads_roots()))
        if found:
            print(f"  e.g. #{found[0].id}: {Path(found[0].video_path.replace(chr(92), '/')).name}")
        if missing:
            print("\nMissing recordings (first 15):")
            for rec in missing[:15]:
                name = Path(rec.video_path.replace("\\", "/")).name
                print(f"  #{rec.id:<5} {rec.original_filename or name}   expected file name: {name}")
            print("\nCopy the 'uploads' folder (with its 'videos' and 'audio' folders) from the computer that "
                  "received these recordings into this project folder, then run this check again.")
    finally:
        db.close()


if __name__ == "__main__":
    main()