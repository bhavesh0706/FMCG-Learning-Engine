"""
File storage for recordings, the analysis queue and recipe attachments.

The database stores a STORAGE KEY, not a path on one computer:
    videos/1730012345_interview.mp4
and this service joins it with the folder configured on THIS computer (backend/.env):
    path = storage folder for "videos" + "1730012345_interview.mp4"
So the same database works on Windows, macOS, Linux and a server, in any folder,
for any user name, without editing source code.

Configuration (backend/.env, all optional):
    FMCG_STORAGE_ROOT       base folder for everything below (relative = from the project folder)
    FMCG_RECORDINGS_DIR     recordings          (default: <root>/videos)
    FMCG_AUDIO_DIR          audio recordings    (default: <root>/audio)
    FMCG_QUEUE_DIR          uploads waiting for analysis (default: <root>/queue)
    FMCG_ATTACHMENTS_DIR    recipe attachments  (default: <root>/recipe_files)
    FMCG_LEGACY_STORAGE_ROOTS  extra folders (separated by ";") where older installs kept files
Without FMCG_STORAGE_ROOT the previous locations are kept, so nothing has to move:
recordings and audio in <project>/uploads, queue and attachments in backend/uploads.

Older rows hold absolute paths from the computer that saved them
(C:\\Users\\name\\...\\uploads\\videos\\x.mp4 or /Users/name/.../uploads/videos/x.mp4).
resolve() still finds those files here: by the part after "uploads/", then by the file
name in the matching area. Every resolved path must lie inside a storage folder.

The Storage interface keeps the rest of the application independent of where files
live, so an S3-compatible implementation can be added later (FMCG_STORAGE_BACKEND).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

log = logging.getLogger("fmcg.storage")

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent
AREAS = ("videos", "audio", "queue", "recipe_files")
_KEY = re.compile(r"^(videos|audio|queue|recipe_files)/[^/\\]+$")


def _folder(value: Optional[str]) -> Optional[Path]:
    """A configured folder: absolute as given, relative = from the project folder."""
    if not value or not value.strip():
        return None
    p = Path(os.path.expanduser(os.path.expandvars(value.strip())))
    return p if p.is_absolute() else (PROJECT_ROOT / p)


class Storage:
    """Where files are kept. LocalStorage now; an S3-compatible one can follow."""

    def area_dir(self, area: str) -> Path:
        raise NotImplementedError

    def key_for(self, path) -> Optional[str]:
        raise NotImplementedError

    def path_for(self, key: str) -> Optional[Path]:
        raise NotImplementedError

    def resolve(self, stored: Optional[str]) -> Optional[Path]:
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, env=None):
        env = os.environ if env is None else env
        root = _folder(env.get("FMCG_STORAGE_ROOT"))
        defaults = {
            "videos": (root / "videos") if root else PROJECT_ROOT / "uploads" / "videos",
            "audio": (root / "audio") if root else PROJECT_ROOT / "uploads" / "audio",
            "queue": (root / "queue") if root else BACKEND_DIR / "uploads" / "queue",
            "recipe_files": (root / "recipe_files") if root else BACKEND_DIR / "uploads" / "recipe_files",
        }
        overrides = {"videos": "FMCG_RECORDINGS_DIR", "audio": "FMCG_AUDIO_DIR", "queue": "FMCG_QUEUE_DIR",
                     "recipe_files": "FMCG_ATTACHMENTS_DIR"}
        self.areas = {a: (_folder(env.get(overrides[a])) or defaults[a]) for a in AREAS}
        legacy = [_folder(x) for x in (env.get("FMCG_LEGACY_STORAGE_ROOTS") or "").split(";")]
        # Folders older installs used: searched for rows saved with absolute paths.
        roots = [root] if root else []
        roots += [PROJECT_ROOT / "uploads", BACKEND_DIR / "uploads"] + [x for x in legacy if x]
        roots += [d.parent for d in self.areas.values()]
        self.legacy_roots = list(dict.fromkeys(roots))

    # ---- folders
    def area_dir(self, area: str) -> Path:
        folder = self.areas[area]
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _allowed(self) -> list[Path]:
        return list(dict.fromkeys(list(self.areas.values()) + self.legacy_roots))

    @staticmethod
    def _inside(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            return False

    def _safe(self, path: Path) -> Optional[Path]:
        return path if any(self._inside(path, r) for r in self._allowed()) else None

    # ---- keys
    def key_for(self, path) -> Optional[str]:
        """The storage key of a file saved in one of the areas ("videos/x.mp4"), else None."""
        if not path:
            return None
        p = Path(path)
        for area, folder in self.areas.items():
            if self._inside(p, folder) and p.resolve().parent == folder.resolve():
                return f"{area}/{p.name}"
        return None

    def path_for(self, key: str) -> Optional[Path]:
        """The file of a storage key on this computer (None for anything that isn't a key)."""
        if not key or not _KEY.match(key.replace("\\", "/")):
            return None
        area, name = key.replace("\\", "/").split("/", 1)
        if name in ("", ".", ".."):
            return None
        return self.areas[area] / name

    def to_stored(self, path) -> str:
        """What to save in the database for a file: its key (absolute only outside the areas)."""
        key = self.key_for(path)
        if key is None:
            log.warning("File %s is outside the storage folders; storing its path.", path)
        return key or str(path)

    # ---- lookup
    def resolve(self, stored: Optional[str]) -> Optional[Path]:
        """The file on this computer for a stored key or an older absolute path, or None."""
        if not stored:
            return None
        text = str(stored).strip()
        keyed = self.path_for(text)
        if keyed is not None:
            return keyed if keyed.is_file() else None
        # Older rows: an absolute path from any computer (Windows or POSIX separators).
        normalised = text.replace("\\", "/")
        is_windows = bool(re.match(r"^[A-Za-z]:/", normalised)) or text.startswith("\\\\")
        name = (PureWindowsPath(text) if is_windows else PurePosixPath(normalised)).name
        direct = Path(text)
        if not is_windows or os.name == "nt":
            if direct.is_absolute() and direct.is_file() and self._safe(direct):
                return direct
        marker = normalised.lower().rfind("/uploads/")
        relative = normalised[marker + len("/uploads/"):] if marker >= 0 else ""
        candidates = []
        if relative:
            parts = PurePosixPath(relative).parts
            if parts and parts[0] in self.areas and len(parts) >= 2:
                candidates.append(self.areas[parts[0]].joinpath(*parts[1:]))
            candidates += [root.joinpath(*parts) for root in self.legacy_roots]
        if name and name not in (".", ".."):
            candidates += [self.areas["videos"] / name, self.areas["audio"] / name]
            candidates += [root / sub / name for root in self.legacy_roots for sub in ("videos", "audio", "")]
        for c in candidates:
            if c.is_file() and self._safe(c):
                return c
        return None

    def public_key(self, stored: Optional[str]) -> Optional[str]:
        """What the API shows: the key (never a server path). Old rows: their key when found here."""
        if not stored:
            return None
        if self.path_for(stored) is not None:
            return stored.replace("\\", "/")
        found = self.resolve(stored)
        key = self.key_for(found) if found else None
        if key:
            return key
        name = PureWindowsPath(stored).name if re.match(r"^[A-Za-z]:[\\/]", stored) else PurePosixPath(stored.replace("\\", "/")).name
        return f"videos/{name}" if name else None


SETTINGS = ("FMCG_STORAGE_BACKEND", "FMCG_STORAGE_ROOT", "FMCG_RECORDINGS_DIR", "FMCG_AUDIO_DIR", "FMCG_QUEUE_DIR",
            "FMCG_ATTACHMENTS_DIR", "FMCG_LEGACY_STORAGE_ROOTS")
_storage: Optional[Storage] = None
_signature: Optional[tuple] = None


def get_storage() -> Storage:
    """The configured storage (FMCG_STORAGE_BACKEND=local; S3 can be added here later).
    Rebuilt whenever a storage setting changes, so it always matches the environment."""
    global _storage, _signature
    signature = tuple(os.environ.get(k) for k in SETTINGS)
    if _storage is None or signature != _signature:
        backend = (os.environ.get("FMCG_STORAGE_BACKEND") or "local").lower()
        if backend != "local":
            raise RuntimeError(f"FMCG_STORAGE_BACKEND={backend} isn't available yet; use local.")
        _storage, _signature = LocalStorage(), signature
    return _storage


def reset_storage() -> None:
    """Re-read the configuration on the next call."""
    global _storage
    _storage = None
