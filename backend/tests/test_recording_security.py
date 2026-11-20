"""
Encrypted consumer recordings and passkey-authorised playback.

A survey person's device encrypts each recording (AES-256-GCM, its own key, wrapped with the
server's RSA public key) and can't decrypt it. The server stores it encrypted, decrypts only
for the analysis (temp file, deleted) and for an Admin given access to the product who enters
the product passkey (or the Super Admin). Every attempt is logged.
"""

import base64
import hashlib
import io
import os
import uuid
from pathlib import Path

import pytest
from conftest import _unique, assign, auth, make_user, open_round
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.database.models import AnalysisJob, FeedbackRecord, ProductPasskey, RecordingAccessLog, SurveyResponse
from app.services import recording_crypto as C

PLAIN = os.urandom(C.CHUNK * 2 + 12345)  # three chunks, the last one partial
FAKE_AI = {
    "duration_seconds": 30, "duration_formatted": "0:30", "part_duration_seconds": 180, "total_parts": 1,
    "transcript": "Tasty and crunchy.", "complete_transcript": "Tasty and crunchy.", "speech_detected": True,
    "summary": {"summary": "The customer liked it.", "sentiment": "Positive", "key_point": "Crunchy"},
    "parts": [{"part": 1, "start": "0:00", "end": "0:30", "transcript": "Tasty and crunchy.",
               "part_summary": {"summary": "Liked it.", "sentiment": "Positive", "key_point": "Crunchy", "feedback": []}}],
}


def device_encrypt(client, user, plain: bytes):
    """What the phone / browser does, with only the PUBLIC key: encrypted bytes and the wrapped key."""
    info = client.get("/recordings/public-key", headers=auth(user)).json()
    assert "PRIVATE" not in info["public_key_pem"] and info["algorithm"] == C.ALGORITHM
    public = serialization.load_der_public_key(base64.b64decode(info["public_key_spki_b64"]))
    data_key = AESGCM.generate_key(bit_length=256)
    out = io.BytesIO()
    C.encrypt_stream(io.BytesIO(plain), out, data_key)
    wrapped = public.encrypt(data_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return out.getvalue(), info["key_id"], base64.b64encode(wrapped).decode()


# ---------------------------------------------------------------- the format
def test_chunked_aes_gcm_round_trip_ranges_and_tampering(tmp_path, db):
    key = AESGCM.generate_key(bit_length=256)
    enc = tmp_path / "r.enc"
    with open(enc, "wb") as f:
        assert C.encrypt_stream(io.BytesIO(PLAIN), f, key) == len(PLAIN)
    raw = enc.read_bytes()
    assert raw.startswith(C.MAGIC) and PLAIN[:64] not in raw, "nothing readable"
    assert C.layout(enc).size == len(PLAIN) and C.verify_file(enc, key) == len(PLAIN)
    for start, end in [(0, None), (5, 99), (C.CHUNK - 3, C.CHUNK + 3), (len(PLAIN) - 10, len(PLAIN) + 50)]:
        stop = len(PLAIN) - 1 if end is None else min(end, len(PLAIN) - 1)
        assert b"".join(C.decrypt_range(enc, key, start, end)) == PLAIN[start:stop + 1]
    with pytest.raises(C.RecordingCryptoError):
        C.verify_file(enc, AESGCM.generate_key(bit_length=256))  # another key
    flipped = tmp_path / "flip.enc"
    data = bytearray(raw)
    data[100] ^= 1
    flipped.write_bytes(bytes(data))
    with pytest.raises(C.RecordingCryptoError):
        C.verify_file(flipped, key)  # altered
    cut = tmp_path / "cut.enc"
    cut.write_bytes(raw[: len(C.MAGIC) + 2 * (4 + C.NONCE + C.CHUNK + C.TAG)])
    with pytest.raises(C.RecordingCryptoError):
        C.verify_file(cut, key)  # the last chunk removed: the new "last" chunk wasn't marked last
    empty = tmp_path / "empty.enc"
    with open(empty, "wb") as f:
        C.encrypt_stream(io.BytesIO(b""), f, key)
    assert C.verify_file(empty, key) == 0


def test_the_private_key_is_stored_encrypted_and_wrapping_round_trips(db):
    from app.database.models import RecordingKey

    key_id, wrapped = C.wrap_key(db, b"k" * 32)
    row = db.get(RecordingKey, key_id)
    assert "BEGIN" not in row.private_enc and "PRIVATE" not in row.private_enc, "Fernet ciphertext only"
    assert C.unwrap_key(db, key_id, wrapped) == b"k" * 32
    with pytest.raises(C.RecordingCryptoError):
        C.unwrap_key(db, key_id, base64.b64encode(b"x" * 384).decode())


# ---------------------------------------------------------------- the whole flow
@pytest.fixture()
def setup(client, db, product, monkeypatch):
    """The organization's passkey, a product with an Admin given access, another Admin without, a survey person."""
    from app.services import ai_client

    seen = {}

    async def fake_analyze(path, filename):
        seen["path"] = str(path)
        seen["bytes"] = Path(path).read_bytes()
        return FAKE_AI

    monkeypatch.setattr(ai_client, "analyze_media", fake_analyze)
    org = make_user(db, role="client", name=_unique("Acme "))
    allowed = make_user(db, role="admin", name=_unique("Vipin "))
    other = make_user(db, role="admin", name=_unique("Rahul "))
    surveyor = make_user(db, name=_unique("Ishaan "))
    open_round(db, product)
    assign(db, product, surveyor)
    _no_passkey(db)
    assert client.put(f"/org/products/{product.id}/recording-access/passkey", headers=auth(org),
                      json={"passkey": "K7QM-9XPD-2RTF-H4WB"}).status_code == 200
    r = client.put(f"/org/products/{product.id}/recording-access/admins", headers=auth(org), json={"user_ids": [allowed.id]})
    assert r.status_code == 200 and [a["user_id"] for a in r.json()["admins"]] == [allowed.id]
    return {"org": org, "allowed": allowed, "other": other, "surveyor": surveyor, "product": product, "seen": seen}


def _no_passkey(db):
    """Each test starts without the organization's passkey (the test database is shared)."""
    from app.database.models import PasskeyShare

    db.query(ProductPasskey).delete()
    db.query(PasskeyShare).delete()
    db.commit()


def _survey(client, person, product):
    """A submitted survey (answers saved), ready for its recording, as the apps do."""
    from conftest import answers_payload, start_survey

    from app.database.database import SessionLocal

    rid = start_survey(client, person, product)["response_id"]
    s = SessionLocal()
    try:
        payload = answers_payload(s, product)
    finally:
        s.close()
    assert client.put(f"/surveys/responses/{rid}/answers", headers=auth(person), json=payload).status_code == 200
    assert client.post(f"/surveys/responses/{rid}/submit", headers=auth(person)).status_code == 200
    return rid


def _upload_encrypted(client, db, person, response_id, sealed, key_id, wrapped):
    """The phone's resumable upload of an encrypted recording, then the analysis job runs."""
    from app.services import analysis_worker

    upload_id = str(uuid.uuid4())
    start = client.post("/uploads/resumable", headers=auth(person), json={
        "upload_id": upload_id, "filename": "feedback.m4a", "total_bytes": len(sealed), "response_id": response_id,
        "sha256": hashlib.sha256(sealed).hexdigest(), "key_id": key_id, "wrapped_key": wrapped})
    assert start.status_code == 200, start.text
    piece = client.put(f"/uploads/resumable/{upload_id}?offset=0", headers={**auth(person), "Content-Type": "application/octet-stream"},
                       content=sealed)
    assert piece.status_code == 200, piece.text
    done = client.post(f"/uploads/resumable/{upload_id}/complete", headers=auth(person))
    assert done.status_code == 200, done.text
    job_id = done.json()["job"]["job_id"]
    db.expire_all()
    job = db.get(AnalysisJob, job_id)
    assert job.enc_wrapped_key == wrapped and job.stored_path.endswith(".enc")
    analysis_worker.run_job(job.id, job.stored_path, job.filename, job.response_id)
    db.expire_all()
    return db.get(AnalysisJob, job_id)


def test_offline_encrypted_recording_is_analysed_stored_encrypted_and_played_only_with_the_passkey(client, db, setup):
    s = setup
    rid = _survey(client, s["surveyor"], s["product"])
    sealed, key_id, wrapped = device_encrypt(client, s["surveyor"], PLAIN)  # done on the phone, offline
    job = _upload_encrypted(client, db, s["surveyor"], rid, sealed, key_id, wrapped)
    assert job.status == "done", job.error

    # The analysis got the plaintext, from a private temp file that is gone now.
    assert s["seen"]["bytes"] == PLAIN and not Path(s["seen"]["path"]).exists()
    rec = db.query(FeedbackRecord).filter(FeedbackRecord.response_id == rid).one()
    stored = C.layout  # noqa: F841
    from app.services.recordings import resolve_recording

    path = resolve_recording(rec.video_path)
    assert rec.media_encrypted == 1 and rec.video_path.endswith(".enc") and C.is_encrypted(path)
    assert PLAIN[:64] not in path.read_bytes(), "encrypted at rest"
    assert not Path(str(path)[: -len(".enc")]).exists(), "no plaintext copy left"
    assert db.get(SurveyResponse, rid).status == "analyzed"

    # Nobody gets the file through the old endpoint, the survey person included.
    for who in (s["surveyor"], s["allowed"], s["org"]):
        r = client.get(f"/feedback/{rec.id}/video", headers=auth(who))
        assert r.status_code in (403, 404)

    # The survey person can't authorise playback, even with the right passkey.
    r = client.post(f"/recordings/{rec.id}/authorise", headers=auth(s["surveyor"]), json={"passkey": "K7QM-9XPD-2RTF-H4WB"})
    assert r.status_code == 403
    # An Admin without access to this product can't either.
    r = client.post(f"/recordings/{rec.id}/authorise", headers=auth(s["other"]), json={"passkey": "K7QM-9XPD-2RTF-H4WB"})
    assert r.status_code == 403 and "haven't been given access" in r.json()["detail"]
    # The allowed Admin with a wrong passkey: refused.
    r = client.post(f"/recordings/{rec.id}/authorise", headers=auth(s["allowed"]), json={"passkey": "WRONG-PASS-KEY0"})
    assert r.status_code == 403 and "Wrong passkey" in r.json()["detail"]
    # With the right passkey (spaces / lower case are fine): a playback token.
    ok = client.post(f"/recordings/{rec.id}/authorise", headers=auth(s["allowed"]), json={"passkey": " k7qm-9xpd-2rtf-h4wb "})
    assert ok.status_code == 200, ok.text
    token = ok.json()["token"]
    assert "key" not in str(ok.json()).lower().replace("passkey", ""), "no key material in the answer"

    full = client.get(f"/recordings/{rec.id}/stream?pt={token}")
    assert full.status_code == 200 and full.content == PLAIN
    assert full.headers["cache-control"].startswith("no-store") and full.headers["content-disposition"] == "inline"
    part = client.get(f"/recordings/{rec.id}/stream?pt={token}", headers={"Range": f"bytes={C.CHUNK - 5}-{C.CHUNK + 4}"})
    assert part.status_code == 206 and part.content == PLAIN[C.CHUNK - 5: C.CHUNK + 5]
    assert part.headers["content-range"] == f"bytes {C.CHUNK - 5}-{C.CHUNK + 4}/{len(PLAIN)}"
    # A token is for one recording only, and a made-up one does nothing.
    assert client.get(f"/recordings/{rec.id + 999}/stream?pt={token}").status_code in (403, 404)
    assert client.get(f"/recordings/{rec.id}/stream?pt=forged").status_code == 403

    # Every attempt is in the log.
    outcomes = [(x.user_id, x.outcome) for x in db.query(RecordingAccessLog).order_by(RecordingAccessLog.id)]
    assert (s["surveyor"].id, "not_authorised") in outcomes and (s["other"].id, "not_authorised") in outcomes
    assert (s["allowed"].id, "wrong_passkey") in outcomes and (s["allowed"].id, "granted") in outcomes
    assert (s["allowed"].id, "played") in outcomes
    hist = client.get(f"/recordings/{rec.id}/history", headers=auth(s["allowed"])).json()["history"]
    assert {h["outcome"] for h in hist} >= {"granted", "played", "wrong_passkey"}
    assert client.get(f"/recordings/{rec.id}/history", headers=auth(s["surveyor"])).status_code == 403

    # Rotating the passkey keeps the recording playable (the passkey isn't the encryption key).
    assert client.put(f"/org/products/{s['product'].id}/recording-access/passkey", headers=auth(s["org"]),
                      json={"passkey": "NEW-PASS-2026-ZZ"}).json()["version"] == 2
    assert client.post(f"/recordings/{rec.id}/authorise", headers=auth(s["allowed"]),
                       json={"passkey": "K7QM-9XPD-2RTF-H4WB"}).status_code == 403, "the old passkey stops working"
    again = client.post(f"/recordings/{rec.id}/authorise", headers=auth(s["allowed"]), json={"passkey": "NEW-PASS-2026-ZZ"}).json()
    assert client.get(f"/recordings/{rec.id}/stream?pt={again['token']}").content == PLAIN

    # The Super Admin (who controls the passkey) can play too; never without it.
    assert client.post(f"/recordings/{rec.id}/authorise", headers=auth(s["org"]), json={"passkey": "nope-nope"}).status_code == 403
    assert client.post(f"/recordings/{rec.id}/authorise", headers=auth(s["org"]), json={"passkey": "NEW-PASS-2026-ZZ"}).status_code == 200


def test_wrong_passkeys_lock_and_the_passkey_is_only_hashed(client, db, setup):
    s = setup
    row = db.query(ProductPasskey).filter_by(product_id=0).one()  # one passkey for the organization
    assert row.passkey_hash.startswith("scrypt$") and "K7QM" not in row.passkey_hash and row.hint == "WB"
    rid = _survey(client, s["surveyor"], s["product"])
    sealed, key_id, wrapped = device_encrypt(client, s["surveyor"], b"short clip")
    _upload_encrypted(client, db, s["surveyor"], rid, sealed, key_id, wrapped)
    rec = db.query(FeedbackRecord).filter(FeedbackRecord.response_id == rid).one()
    for _ in range(5):
        client.post(f"/recordings/{rec.id}/authorise", headers=auth(s["allowed"]), json={"passkey": "guess-guess"})
    locked = client.post(f"/recordings/{rec.id}/authorise", headers=auth(s["allowed"]), json={"passkey": "K7QM-9XPD-2RTF-H4WB"})
    assert locked.status_code == 429 and "Too many wrong passkeys" in locked.json()["detail"]
    assert db.query(RecordingAccessLog).filter_by(user_id=s["allowed"].id, outcome="locked").count() == 1


def test_damaged_or_foreign_encrypted_uploads_are_refused(client, db, setup):
    s = setup
    rid = _survey(client, s["surveyor"], s["product"])
    sealed, key_id, wrapped = device_encrypt(client, s["surveyor"], PLAIN)
    bad = bytearray(sealed)
    bad[-5] ^= 1
    upload_id = str(uuid.uuid4())
    client.post("/uploads/resumable", headers=auth(s["surveyor"]), json={
        "upload_id": upload_id, "filename": "feedback.m4a", "total_bytes": len(bad), "response_id": rid,
        "key_id": key_id, "wrapped_key": wrapped})
    client.put(f"/uploads/resumable/{upload_id}?offset=0", headers={**auth(s["surveyor"]), "Content-Type": "application/octet-stream"},
               content=bytes(bad))
    done = client.post(f"/uploads/resumable/{upload_id}/complete", headers=auth(s["surveyor"]))
    assert done.status_code == 422 and "damaged" in done.json()["detail"]["message"]
    foreign = client.post("/uploads/resumable", headers=auth(s["surveyor"]), json={
        "upload_id": str(uuid.uuid4()), "filename": "feedback.m4a", "total_bytes": 10, "key_id": key_id,
        "wrapped_key": base64.b64encode(os.urandom(384)).decode()})
    assert foreign.status_code == 400 and foreign.json()["detail"]["code"] == "recording_key_unknown"


def test_multipart_upload_from_the_browser_is_encrypted_too(client, db, setup):
    s = setup
    rid = _survey(client, s["surveyor"], s["product"])
    sealed, key_id, wrapped = device_encrypt(client, s["surveyor"], PLAIN)
    r = client.post("/analysis/jobs", headers=auth(s["surveyor"]), files={"file": ("feedback.webm", sealed, "application/octet-stream")},
                    data={"response_id": str(rid), "key_id": str(key_id), "wrapped_key": wrapped})
    assert r.status_code == 202, r.text
    db.expire_all()
    job = db.get(AnalysisJob, r.json()["job_id"])
    from app.services.recordings import resolve_recording

    assert C.is_encrypted(resolve_recording(job.stored_path)), "queued encrypted"


def test_passkey_sharing_inside_the_app_is_one_time(client, db, setup):
    s = setup
    org, allowed, product = s["org"], s["allowed"], s["product"]
    assert client.post(f"/org/products/{product.id}/recording-access/share", headers=auth(org),
                       json={"passkey": "wrong-passkey", "user_ids": [allowed.id]}).status_code == 400
    assert client.post(f"/org/products/{product.id}/recording-access/share", headers=auth(org),
                       json={"passkey": "K7QM-9XPD-2RTF-H4WB", "user_ids": [s["other"].id]}).status_code == 400, "not allowed yet"
    sent = client.post(f"/org/products/{product.id}/recording-access/share", headers=auth(org),
                       json={"passkey": "K7QM-9XPD-2RTF-H4WB", "user_ids": [allowed.id]})
    assert sent.status_code == 200 and sent.json()["admins"][0]["share_pending"] is True
    mine = client.get("/admin/recording-access", headers=auth(allowed)).json()["products"]
    assert mine == [{"product_id": product.id, "name": product.name, "passkey_waiting": True}]
    assert client.post(f"/admin/recording-access/{product.id}/reveal", headers=auth(s["other"])).status_code == 403
    shown = client.post(f"/admin/recording-access/{product.id}/reveal", headers=auth(allowed))
    assert shown.status_code == 200 and shown.json()["passkey"] == "K7QM-9XPD-2RTF-H4WB"
    assert client.post(f"/admin/recording-access/{product.id}/reveal", headers=auth(allowed)).status_code == 404, "only once"
    # The Super Admin's status never contains the passkey.
    status = client.get(f"/org/products/{product.id}/recording-access", headers=auth(org)).json()
    assert "K7QM" not in str(status) and status["hint"] == "WB"
    # Admins and survey persons can't manage passkeys.
    for who in (allowed, s["surveyor"]):
        assert client.put(f"/org/products/{product.id}/recording-access/passkey", headers=auth(who),
                          json={"passkey": "mine-mine-mine"}).status_code == 403


def test_super_admin_sets_the_passkey_when_creating_the_product(client, db):
    _no_passkey(db)
    org = make_user(db, role="client")
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    short = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Peanuts "), "recording_passkey": "abc"})
    assert short.status_code == 400 and "at least 8" in short.json()["detail"]
    r = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Peanuts "),
                                                         "recording_passkey": "Q2W3-E4R5-T6Y7"})
    assert r.status_code == 201, r.text
    assert "Q2W3" not in r.text, "never echoed"
    assert db.query(ProductPasskey).filter_by(product_id=0).one().version == 1, "the organization's passkey"
    # Once it exists, adding another product never changes it.
    again = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Peanuts "),
                                                             "recording_passkey": "OTHER-PASS-KEY-99"})
    assert again.status_code == 201
    db.expire_all()
    row = db.query(ProductPasskey).one()
    from app.services.recording_access import passkey_matches

    assert row.version == 1 and passkey_matches(row.passkey_hash, "Q2W3-E4R5-T6Y7")
    assert client.get("/org/recording-passkey", headers=auth(org)).json()["has_passkey"] is True


def test_existing_plaintext_recordings_are_encrypted_in_place(client, db, product, tmp_path):
    """The one-time tool for recordings stored before encryption: verified, then the plaintext goes."""
    from conftest import playback_token

    from app.services.storage import get_storage
    from app.tools import encrypt_recordings

    plain = get_storage().area_dir("videos") / f"old_{uuid.uuid4().hex}.mp3"
    plain.write_bytes(b"ID3 old recording" * 1000)
    outside = tmp_path / "elsewhere.mp3"
    outside.write_bytes(b"not ours")
    rec = FeedbackRecord(original_filename="old.mp3", video_path=get_storage().to_stored(plain), duration_seconds=5.0)
    other = FeedbackRecord(original_filename="x.mp3", video_path=str(outside), duration_seconds=5.0)
    db.add_all([rec, other])
    db.commit()

    dry = encrypt_recordings.run(apply=False)
    assert dry["would_encrypt"] >= 1 and plain.exists(), "a dry run changes nothing"
    done = encrypt_recordings.run(apply=True)
    assert done["encrypted"] >= 1 and done["outside_storage"] + done["missing"] >= 1
    db.expire_all()
    rec = db.get(FeedbackRecord, rec.id)
    assert rec.media_encrypted == 1 and rec.video_path.endswith(".enc") and not plain.exists()
    assert outside.exists() and db.get(FeedbackRecord, other.id).media_encrypted == 0, "files elsewhere are never touched"
    admin = make_user(db, role="admin")
    assert client.get(f"/recordings/{rec.id}/stream?pt={playback_token(db, admin, rec.id)}").content == b"ID3 old recording" * 1000
    assert encrypt_recordings.run(apply=True)["encrypted"] == 0, "running again does nothing"


def test_one_passkey_opens_the_recordings_of_every_product(client, db, setup):
    """The same passkey for all products; each product still has its own allowed Admins."""
    from conftest import _unique as u

    s = setup
    org, allowed = s["org"], s["allowed"]
    cid = client.post("/categories", headers=auth(org), json={"name": u("Dairy ")}).json()["id"]
    lassi = client.post("/products", headers=auth(org), json={"category_id": cid, "name": u("Lassi ")}).json()["id"]
    status = client.get(f"/org/products/{lassi}/recording-access", headers=auth(org)).json()
    assert status["has_passkey"] is True and status["scope"] == "organization" and status["admins"] == []
    assert client.put(f"/org/products/{lassi}/recording-access/admins", headers=auth(org),
                      json={"user_ids": [allowed.id]}).status_code == 200
    # Changing it from the organization page changes it for every product.
    changed = client.put("/org/recording-passkey", headers=auth(org), json={"passkey": "ONE-KEY-FOR-ALL-1"})
    assert changed.status_code == 200 and changed.json()["version"] == 2 and "ONE-KEY" not in changed.text
    assert client.get(f"/org/products/{s['product'].id}/recording-access", headers=auth(org)).json()["version"] == 2
    # Sent once, revealed once, for all products.
    assert client.post("/org/recording-passkey/share", headers=auth(org),
                       json={"passkey": "ONE-KEY-FOR-ALL-1", "user_ids": [allowed.id]}).status_code == 200
    mine = client.get("/admin/recording-access", headers=auth(allowed)).json()
    assert mine["passkey_waiting"] is True and len(mine["products"]) == 2
    shown = client.post("/admin/recording-access/reveal", headers=auth(allowed))
    assert shown.status_code == 200 and shown.json()["passkey"] == "ONE-KEY-FOR-ALL-1"
    assert client.get("/admin/recording-access", headers=auth(allowed)).json()["passkey_waiting"] is False
    for who in (allowed, s["surveyor"]):
        assert client.put("/org/recording-passkey", headers=auth(who), json={"passkey": "mine-mine-mine"}).status_code == 403
