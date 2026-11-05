"""
Finding a recording on this computer.

feedback_records.video_path stores a storage key ("videos/123_x.mp3", services/storage.py).
Older rows hold the full path where the upload was saved, on the
computer that received it, for example
    C:\\Users\\iONE IT\\Documents\\FMCG-Learning-Engine\\uploads\\videos\\123_x.mp3
After the data moves to another computer (Windows -> Mac, another folder, a restored
backup) that exact path no longer exists, although the file is in this project's
uploads folder. scripts/import_data.ps1 rewrites the paths on Windows; this lookup makes
every computer find them without changing any stored data:

  1. the stored path, when it exists (normal case: nothing changes);
  2. the part after "uploads/" (Windows or POSIX separators) under this computer's
     uploads folder (FMCG_RECORDINGS_DIR's parent, and <project>/uploads);
  3. the file name in uploads/videos or uploads/audio.

Only files inside an uploads folder are ever returned.
"""

from __future__ import annotations

from pathlib import Path

from app.services.storage import get_storage


def uploads_roots() -> list[Path]:
    """The folders searched for recordings on this computer (storage areas and older roots)."""
    st = get_storage()
    return list(dict.fromkeys([st.areas["videos"], st.areas["audio"]] + st.legacy_roots))


def resolve_recording(stored: str | None) -> Path | None:
    """The recording's file on this computer (a storage key or an older absolute path), or None.
    See services/storage.py: keys are joined with this computer's configured folders."""
    return get_storage().resolve(stored)
