"""
Finding a recording on this computer.

feedback_records.video_path stores the full path where the upload was saved, on the
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

import os
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def uploads_roots() -> list[Path]:
    """The uploads folders of this installation (first = where new uploads go)."""
    roots: list[Path] = []
    configured = os.environ.get("FMCG_RECORDINGS_DIR")
    if configured:
        roots.append(Path(configured).parent)  # .../uploads (recordings go to .../uploads/videos)
    roots.append(PROJECT_ROOT / "uploads")
    unique: list[Path] = []
    for r in roots:
        if r not in unique:
            unique.append(r)
    return unique


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def resolve_recording(stored: str | None) -> Path | None:
    """The recording's file on this computer, or None when it isn't here."""
    if not stored:
        return None
    direct = Path(stored)
    if direct.is_file():
        return direct

    normalised = stored.replace("\\", "/")
    marker = normalised.lower().rfind("/uploads/")
    relative = normalised[marker + len("/uploads/"):] if marker >= 0 else ""
    name = PurePosixPath(normalised).name

    configured = os.environ.get("FMCG_RECORDINGS_DIR")
    if configured and name:
        candidate = Path(configured) / name  # recordings kept elsewhere (e.g. a mounted disk)
        if candidate.is_file() and _inside(candidate, Path(configured)):
            return candidate

    for root in uploads_roots():
        candidates = []
        if relative:
            candidates.append(root / Path(*PurePosixPath(relative).parts))
        if name:
            candidates += [root / "videos" / name, root / "audio" / name, root / name]
        for candidate in candidates:
            if candidate.is_file() and _inside(candidate, root):
                return candidate
    return None