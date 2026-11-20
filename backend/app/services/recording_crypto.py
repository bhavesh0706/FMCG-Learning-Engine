"""
Encrypted consumer recordings (audio / video feedback).

Envelope encryption, the same on Android, iOS, the web and here:

  1. The device makes a fresh random 256-bit data key for each recording and encrypts the
     recording with AES-256-GCM in 1 MiB chunks (format below) as soon as recording stops.
  2. It wraps (encrypts) the data key with the server's RSA-OAEP-SHA256 PUBLIC key and
     forgets it. The device keeps only the encrypted file and the wrapped key: it can't
     decrypt either, so a survey person can't play the recording. Works offline (the public
     key is downloaded with the survey data).
  3. The server stores the encrypted file as it arrived (encrypted at rest) with the wrapped
     key. Its RSA PRIVATE key is kept in the database encrypted with the master key
     (Fernet), which lives outside the project: FMCG_MASTER_KEY, FMCG_MASTER_KEY_FILE or
     ~/.fmcg/master.key (the same master key as the AI service's secrets).
  4. Only the server unwraps a data key: for the analysis (decrypted into a private temp
     file, deleted afterwards) and for playback after an Admin's product passkey is
     verified (services/recording_access.py). Data keys and the private key never leave the
     server; the passkey is an authorisation, not an encryption key, so rotating it keeps
     every recording playable.

File format (FMCGREC1), chunked so a recording can be streamed and sought without
decrypting the whole file:

    "FMCGREC1"                                     8-byte magic
    repeated: len(4, big-endian) | nonce(12) | ciphertext+tag(len-12)
              plaintext of every chunk is CHUNK bytes except the last
              AAD = "FMCGREC1" | index (4, big-endian) | last (1 byte: 1 or 0)

The AAD binds each chunk to its position and marks the last one, so chunks can't be
reordered, dropped or the file truncated without the decryption failing.
"""

from __future__ import annotations

import base64
import os
import struct
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"FMCGREC1"
CHUNK = 1024 * 1024  # plaintext bytes per chunk
NONCE = 12
TAG = 16
ALGORITHM = "RSA-OAEP-256/AES-256-GCM/FMCGREC1"
RSA_BITS = 3072

DEFAULT_MASTER_KEY_FILE = Path.home() / ".fmcg" / "master.key"


class RecordingCryptoError(RuntimeError):
    """A recording could not be encrypted or decrypted (wrong key, damaged or truncated file)."""


# ---------------------------------------------------------------- master key (outside the project)
def _master_key_file() -> Path:
    custom = os.environ.get("FMCG_MASTER_KEY_FILE", "").strip()
    return Path(custom).expanduser() if custom else DEFAULT_MASTER_KEY_FILE


def master_fernet() -> Fernet:
    """The master key: FMCG_MASTER_KEY, else the key file. Created once (0600) when missing,
    so a fresh installation works; production sets FMCG_MASTER_KEY or keeps the file safe."""
    value = os.environ.get("FMCG_MASTER_KEY", "").strip()
    if not value:
        path = _master_key_file()
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
        else:
            value = Fernet.generate_key().decode()
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value)
    try:
        return Fernet(value.encode())
    except ValueError as exc:
        raise RecordingCryptoError("The master key is not a valid Fernet key (44 characters).") from exc


def seal_secret(text: str) -> str:
    """A short secret encrypted with the master key (e.g. a passkey waiting to be revealed once)."""
    return master_fernet().encrypt(text.encode()).decode()


def open_secret(token: str) -> str:
    try:
        return master_fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise RecordingCryptoError("The secret can't be opened with this server's master key.") from exc


# ---------------------------------------------------------------- the server's key pair
_lock = threading.Lock()
_private_cache: dict = {}


def _new_key_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=RSA_BITS)
    public_pem = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
    return public_pem, master_fernet().encrypt(private_pem).decode()


def active_key(db):
    """The recording key pair in use (created the first time). Returns the RecordingKey row."""
    from app.database.models import RecordingKey

    key = db.query(RecordingKey).filter(RecordingKey.is_active == 1).order_by(RecordingKey.id.desc()).first()
    if key is None:
        with _lock:
            key = db.query(RecordingKey).filter(RecordingKey.is_active == 1).order_by(RecordingKey.id.desc()).first()
            if key is None:
                public_pem, private_enc = _new_key_pair()
                key = RecordingKey(public_pem=public_pem, private_enc=private_enc, algorithm=ALGORITHM, is_active=1)
                db.add(key)
                db.commit()
                db.refresh(key)
    return key


def public_key_info(db) -> dict:
    """What devices need to encrypt offline: never anything secret."""
    key = active_key(db)
    der = serialization.load_pem_public_key(key.public_pem.encode()).public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return {"key_id": key.id, "algorithm": ALGORITHM, "public_key_pem": key.public_pem,
            "public_key_spki_b64": base64.b64encode(der).decode(), "chunk_size": CHUNK}


def _private_key(db, key_id: int):
    from app.database.models import RecordingKey

    cached = _private_cache.get(key_id)
    if cached is not None:
        return cached
    key = db.get(RecordingKey, key_id)
    if key is None:
        raise RecordingCryptoError(f"Recording key #{key_id} is unknown on this server.")
    try:
        pem = master_fernet().decrypt(key.private_enc.encode())
    except InvalidToken as exc:
        raise RecordingCryptoError("The recording key can't be unlocked: the master key is not the one it was made with.") from exc
    private = serialization.load_pem_private_key(pem, password=None)
    _private_cache[key_id] = private
    return private


_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def wrap_key(db, data_key: bytes) -> tuple[int, str]:
    """(key_id, wrapped key b64): what a device does with the public key (also used here)."""
    key = active_key(db)
    public = serialization.load_pem_public_key(key.public_pem.encode())
    return key.id, base64.b64encode(public.encrypt(data_key, _OAEP)).decode()


def unwrap_key(db, key_id: int, wrapped_b64: str) -> bytes:
    """The recording's data key. Server-side only: never returned by any API."""
    try:
        data_key = _private_key(db, key_id).decrypt(base64.b64decode(wrapped_b64), _OAEP)
    except (ValueError, TypeError) as exc:
        raise RecordingCryptoError("The recording's wrapped key doesn't open with this server's key.") from exc
    if len(data_key) != 32:
        raise RecordingCryptoError("The recording's data key has the wrong length.")
    return data_key


# ---------------------------------------------------------------- the FMCGREC1 format
def _aad(index: int, last: bool) -> bytes:
    return MAGIC + struct.pack(">IB", index, 1 if last else 0)


def encrypt_stream(src: BinaryIO, dst: BinaryIO, data_key: bytes) -> int:
    """Encrypts src into dst (FMCGREC1). Returns the plaintext size."""
    aes = AESGCM(data_key)
    dst.write(MAGIC)
    index, total = 0, 0
    chunk = src.read(CHUNK)
    while True:
        following = src.read(CHUNK) if len(chunk) == CHUNK else b""
        last = not following
        nonce = os.urandom(NONCE)
        sealed = aes.encrypt(nonce, chunk, _aad(index, last))
        dst.write(struct.pack(">I", NONCE + len(sealed)) + nonce + sealed)
        total += len(chunk)
        index += 1
        if last:
            return total
        chunk = following


def is_encrypted(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


@dataclass
class _Layout:
    size: int  # plaintext size
    chunks: int


def layout(path: Path) -> _Layout:
    """Plaintext size from the file size (every chunk but the last is full)."""
    raw = os.path.getsize(path) - len(MAGIC)
    full = 4 + NONCE + CHUNK + TAG
    if raw <= 0:
        raise RecordingCryptoError("The encrypted recording is empty.")
    chunks, rest = divmod(raw, full)
    if rest:
        if rest < 4 + NONCE + TAG:
            raise RecordingCryptoError("The encrypted recording is damaged (incomplete chunk).")
        return _Layout(chunks * CHUNK + rest - (4 + NONCE + TAG), chunks + 1)
    return _Layout(chunks * CHUNK, chunks)


def _read_chunk(f: BinaryIO, aes: AESGCM, index: int, chunks: int) -> bytes:
    f.seek(len(MAGIC) + index * (4 + NONCE + CHUNK + TAG))
    head = f.read(4)
    if len(head) != 4:
        raise RecordingCryptoError("The encrypted recording is truncated.")
    (length,) = struct.unpack(">I", head)
    body = f.read(length)
    if len(body) != length or length < NONCE + TAG:
        raise RecordingCryptoError("The encrypted recording is truncated.")
    try:
        return aes.decrypt(body[:NONCE], body[NONCE:], _aad(index, index == chunks - 1))
    except Exception as exc:  # cryptography.exceptions.InvalidTag
        raise RecordingCryptoError("The recording failed its integrity check (wrong key or altered file).") from exc


def decrypt_range(path: Path, data_key: bytes, start: int = 0, end: Optional[int] = None) -> Iterator[bytes]:
    """Plaintext bytes start..end (inclusive), decrypting only the chunks needed (seeking in a video)."""
    info = layout(path)
    end = info.size - 1 if end is None else min(end, info.size - 1)
    if start > end:
        return
    aes = AESGCM(data_key)
    with open(path, "rb") as f:
        for index in range(start // CHUNK, end // CHUNK + 1):
            plain = _read_chunk(f, aes, index, info.chunks)
            lo = start - index * CHUNK if index == start // CHUNK else 0
            hi = end - index * CHUNK + 1 if index == end // CHUNK else len(plain)
            yield plain[lo:hi]


def verify_file(path: Path, data_key: bytes) -> int:
    """Decrypts every chunk (nothing kept) to prove the file and its key belong together. Returns the size."""
    info = layout(path)
    aes = AESGCM(data_key)
    with open(path, "rb") as f:
        for index in range(info.chunks):
            _read_chunk(f, aes, index, info.chunks)
    return info.size


def decrypt_to_temp(path: Path, data_key: bytes, suffix: str = "") -> Path:
    """A private (0600) temporary plaintext copy, for the transcription only. The caller deletes it."""
    fd, name = tempfile.mkstemp(prefix="fmcg-rec-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as out:
            for part in decrypt_range(path, data_key):
                out.write(part)
        os.chmod(name, 0o600)
        return Path(name)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


def encrypt_file(db, plain_path: Path, out_path: Path) -> tuple[int, str]:
    """Encrypts a plaintext recording on the server (an upload from an older app version, or an
    existing recording). Returns (key_id, wrapped key)."""
    data_key = AESGCM.generate_key(bit_length=256)
    tmp = out_path.with_name(out_path.name + ".part")
    with open(plain_path, "rb") as src, open(tmp, "wb") as dst:
        encrypt_stream(src, dst, data_key)
    os.replace(tmp, out_path)
    return wrap_key(db, data_key)
