"""
Saving uploaded recordings safely (SEC-08).

  - size cap: FMCG_MAX_UPLOAD_MB (default 500 MB; a 35-minute HD video is well
    under this). Larger uploads stop with HTTP 413 and the partial file is removed.
  - the file is copied in 1 MB chunks, never read into memory at once.
  - the stored name keeps only the base name the client sent (no folders), so a
    name like "../../x" can never write outside the target folder.

The HTTPS proxy (Caddy, docker/Caddyfile) enforces a slightly larger body limit
first, so an oversized upload is usually refused before it reaches the API.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from fastapi import HTTPException, UploadFile

CHUNK = 1024 * 1024
MAX_UPLOAD_BYTES = int(float(os.environ.get("FMCG_MAX_UPLOAD_MB", "") or 500) * 1024 * 1024)


def safe_name(filename: str | None, default: str = "recording") -> str:
    """The client's file name without folders or unusual characters."""
    name = Path(str(filename or "").replace("\\", "/")).name.strip()
    name = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", name)[:150].strip(" .")
    return name or default


def save_upload(file: UploadFile, folder: Path, name: str) -> Path:
    """
    Copy the upload into folder/<timestamp>_<name>. Raises 413 above the size cap
    and 400 for an empty file; nothing is left behind on either error.
    """
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{int(time.time() * 1000)}_{safe_name(name)}"
    limit = MAX_UPLOAD_BYTES
    written = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = file.file.read(CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"The file is larger than {limit // (1024 * 1024)} MB. Record a shorter clip or compress it.",
                    )
                out.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The file is empty. Record or choose the file again.")
    return target
