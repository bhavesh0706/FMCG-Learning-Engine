"""
Encrypt the recordings stored before recording encryption existed (feedback_records with
media_encrypted = 0), in place: <file>.enc with its own data key (wrapped with the server's
key), checked by decrypting it, then the plaintext is deleted and the record updated.

    python -m app.tools.encrypt_recordings            # dry run: what would be encrypted
    python -m app.tools.encrypt_recordings --apply    # do it

Only files inside the storage folders are touched; a missing file is reported and skipped.
Safe to run again (encrypted records are skipped). Needs the master key (FMCG_MASTER_KEY or
~/.fmcg/master.key) that the server uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.database.database import SessionLocal
from app.database.models import FeedbackRecord
from app.services import recording_crypto as C
from app.services.recordings import resolve_recording
from app.services.storage import get_storage


def run(apply: bool) -> dict:
    db = SessionLocal()
    counts = {"encrypted": 0, "would_encrypt": 0, "missing": 0, "outside_storage": 0, "already": 0, "failed": 0}
    try:
        for rec in db.query(FeedbackRecord).order_by(FeedbackRecord.id).all():
            if rec.media_encrypted:
                counts["already"] += 1
                continue
            path = resolve_recording(rec.video_path) if rec.video_path else None
            if path is None:
                counts["missing"] += 1
                print(f"  #{rec.id}: file not on this computer, skipped")
                continue
            if get_storage().key_for(path) is None:
                counts["outside_storage"] += 1
                print(f"  #{rec.id}: {path} is outside the storage folders, skipped")
                continue
            if C.is_encrypted(path):  # encrypted file but the record wasn't updated: leave for a person to check
                counts["failed"] += 1
                print(f"  #{rec.id}: the file is already FMCGREC1 but the record isn't: skipped, check it")
                continue
            if not apply:
                counts["would_encrypt"] += 1
                continue
            target = Path(str(path) + ".enc")
            try:
                key_id, wrapped = C.encrypt_file(db, path, target)
                size = C.verify_file(target, C.unwrap_key(db, key_id, wrapped))
                if size != path.stat().st_size:
                    raise C.RecordingCryptoError("size mismatch after encryption")
                rec.video_path = get_storage().to_stored(target)
                rec.media_encrypted, rec.media_key_id, rec.media_wrapped_key = 1, key_id, wrapped
                db.commit()  # the record points to the encrypted copy BEFORE the plaintext goes
                path.unlink()
                counts["encrypted"] += 1
            except Exception as e:  # nothing deleted: the plaintext and the record stay as they were
                db.rollback()
                target.unlink(missing_ok=True)
                counts["failed"] += 1
                print(f"  #{rec.id}: not encrypted ({e})")
    finally:
        db.close()
    return counts


def main(argv=None) -> None:
    apply = "--apply" in (argv if argv is not None else sys.argv[1:])
    counts = run(apply)
    print(("Encrypted" if apply else "Dry run (add --apply to encrypt)") + ": " + ", ".join(f"{k} {v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
