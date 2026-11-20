"""
Encrypted secrets for the AI service (currently the OpenAI API key).

ai_service/.env holds only the ENCRYPTED key:
    OPENAI_API_KEY_ENC=gAAAAA...

The master key that unlocks it is kept OUTSIDE the project, read from (first match):
    1. FMCG_MASTER_KEY        environment variable (use this for Docker / servers)
    2. FMCG_MASTER_KEY_FILE   path to a file holding the master key
    3. ~/.fmcg/master.key     default file, created by tools/encrypt_openai_key.py

Encryption is Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` package.
To create or change the encrypted key run:
    python tools/encrypt_openai_key.py
"""

import os
from pathlib import Path

DEFAULT_MASTER_KEY_FILE = Path.home() / ".fmcg" / "master.key"


class SecretError(RuntimeError):
    """The encrypted secret could not be unlocked; the message says why."""


def master_key_file() -> Path:
    custom = os.environ.get("FMCG_MASTER_KEY_FILE", "").strip()
    return Path(custom).expanduser() if custom else DEFAULT_MASTER_KEY_FILE


def load_master_key() -> bytes:
    value = os.environ.get("FMCG_MASTER_KEY", "").strip()
    if value:
        return value.encode()
    path = master_key_file()
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value.encode()
    raise SecretError(
        "No master key found to unlock OPENAI_API_KEY_ENC. Set FMCG_MASTER_KEY, "
        f"or put it in {path} (run: python tools/encrypt_openai_key.py)."
    )


def decrypt(token: str) -> str:
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:
        raise SecretError("The 'cryptography' package is missing: pip install cryptography") from exc
    try:
        fernet = Fernet(load_master_key())
    except ValueError as exc:
        raise SecretError("The master key is not a valid Fernet key (it must be 44 characters).") from exc
    try:
        return fernet.decrypt(token.strip().encode()).decode()
    except InvalidToken as exc:
        raise SecretError(
            "OPENAI_API_KEY_ENC could not be decrypted: the master key does not match "
            "the one it was encrypted with, or the value was changed."
        ) from exc


def openai_api_key() -> str:
    """The OpenAI key: decrypted from OPENAI_API_KEY_ENC, else plain OPENAI_API_KEY, else ''."""
    encrypted = os.environ.get("OPENAI_API_KEY_ENC", "").strip()
    if encrypted:
        return decrypt(encrypted)
    return os.environ.get("OPENAI_API_KEY", "").strip()
