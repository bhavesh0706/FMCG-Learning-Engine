"""
Is the recording path problem solved on THIS computer?

    python -m app.tools.verify_storage

Read-only for your data: it only writes (and then deletes) one small test file in each
storage folder. It checks:
  1. the storage folders this computer uses (from backend/.env), and that they are writable
  2. a round trip in each folder: save -> store the key -> find it by the key -> delete
  3. every file reference in MySQL (recordings, queued uploads, recipe attachments):
       key        stored as "videos/x.mp4"           (computer-independent: fixed)
       old+found  an older full path, file found here (works; convert with storage_keys)
       missing    the file isn't on this computer     (copy the uploads folder over)
  4. full paths that come from another computer (a Windows path on a Mac or the reverse)
and ends with a verdict. Exit code 0 = solved, 1 = something to fix.
"""

from __future__ import annotations

import os
import re
import sys
import uuid

from app.database.database import SessionLocal
from app.database.models import AnalysisJob, FeedbackRecord, RecipeAttachment
from app.services.storage import PROJECT_ROOT, get_storage

TABLES = [("Recordings", FeedbackRecord, "video_path"), ("Queued uploads", AnalysisJob, "stored_path"),
          ("Recipe attachments", RecipeAttachment, "stored_path")]
WINDOWS = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")


def _line(ok: bool, text: str) -> bool:
    print(("  OK    " if ok else "  FIX   ") + text)
    return ok


def main() -> int:
    st = get_storage()
    problems = 0
    here = "Windows" if os.name == "nt" else ("macOS" if sys.platform == "darwin" else "Linux")
    print(f"Computer: {here} · project folder: {PROJECT_ROOT}")
    print(f"Settings: FMCG_STORAGE_ROOT={os.environ.get('FMCG_STORAGE_ROOT') or '(not set: default folders)'}")

    print("\n1. Storage folders")
    for area in st.areas:
        folder = st.area_dir(area)
        writable = os.access(folder, os.W_OK)
        problems += not _line(folder.is_dir() and writable, f"{area:13} {folder}" + ("" if writable else "  (not writable)"))

    print("\n2. Save -> key -> find again")
    for area in st.areas:
        test = st.area_dir(area) / f"verify_{uuid.uuid4().hex[:8]}.tmp"
        try:
            test.write_bytes(b"storage check")
            key = st.to_stored(test)
            found = st.resolve(key)
            ok = key == f"{area}/{test.name}" and found is not None and found.read_bytes() == b"storage check"
            problems += not _line(ok, f"{area:13} stored as '{key}' and found again" if ok else f"{area:13} round trip failed ({key!r} -> {found})")
        finally:
            test.unlink(missing_ok=True)

    print("\n3. File references in the database")
    db = SessionLocal()
    try:
        for label, model, column in TABLES:
            keys = found = missing = foreign = finished = 0
            examples = []
            for row in db.query(model).filter(getattr(model, column).isnot(None)):
                value = getattr(row, column)
                # A queued upload is only needed until it's analysed: the worker then deletes
                # it on purpose (the recording itself is kept in "videos").
                if model is AnalysisJob and (row.status or "") not in ("queued", "processing"):
                    finished += 1
                    continue
                if st.path_for(value) is not None:
                    if st.resolve(value) is not None:
                        keys += 1
                    else:
                        missing += 1
                        examples.append(f"#{row.id} {value}")
                    continue
                if (WINDOWS.match(value) is not None) != (os.name == "nt"):
                    foreign += 1
                if st.resolve(value) is not None:
                    found += 1
                else:
                    missing += 1
                    examples.append(f"#{row.id} {value}")
            total = keys + found + missing + finished
            print(f"  {label}: {total} · {keys} keys · {found} older full paths found here · {missing} missing"
                  + (f" · {foreign} from another computer type" if foreign else "")
                  + (f" · {finished} finished (temporary copy removed after analysis, as designed)" if finished else ""))
            for e in examples:
                print(f"      missing: {e}")
            if missing:
                problems += 1
                print("      -> copy the uploads folder (videos/audio/queue/recipe_files) from the computer that has these files")
            if found:
                print("      -> optional: python -m app.tools.storage_keys --apply  (stores them as keys; backup CSV first)")
    finally:
        db.close()

    print("\nVerdict: " + ("SOLVED on this computer: every reference resolves through the configured folders."
                           if problems == 0 else f"{problems} thing(s) to fix above."))
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())