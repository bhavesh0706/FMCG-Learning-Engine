"""
Storage service (services/storage.py): environment folders, storage keys, older
absolute paths from Windows and macOS computers, safety, and the API workflows.
These tests run on Linux; Windows and macOS paths are read as text (PureWindowsPath /
PurePosixPath), which is exactly what happens when a database moves between computers.
"""

import csv

import pytest
from conftest import auth, make_user, start_survey

from app.database.models import AnalysisJob, FeedbackRecord, RecipeAttachment
from app.services import storage as storage_mod
from app.services.storage import LocalStorage, get_storage


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A storage root in a temporary folder (like a fresh clone with FMCG_STORAGE_ROOT set)."""
    for key in storage_mod.SETTINGS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FMCG_STORAGE_ROOT", str(tmp_path / "storage"))
    st = get_storage()
    assert st.areas["videos"] == tmp_path / "storage" / "videos"
    return st


def _put(st, area, name, data=b"media"):
    path = st.area_dir(area) / name
    path.write_bytes(data)
    return path


# ---------- configuration ----------

def test_folders_come_from_the_environment_and_are_created(tmp_path):
    st = LocalStorage({"FMCG_STORAGE_ROOT": str(tmp_path / "root"), "FMCG_QUEUE_DIR": str(tmp_path / "elsewhere" / "q")})
    assert st.areas["videos"] == tmp_path / "root" / "videos"
    assert st.areas["recipe_files"] == tmp_path / "root" / "recipe_files"
    assert st.areas["queue"] == tmp_path / "elsewhere" / "q", "a per-folder setting wins"
    assert st.area_dir("videos").is_dir() and st.area_dir("queue").is_dir()


def test_a_relative_storage_root_follows_the_project_folder():
    st = LocalStorage({"FMCG_STORAGE_ROOT": "uploads"})
    assert st.areas["videos"] == storage_mod.PROJECT_ROOT / "uploads" / "videos"


def test_without_settings_the_previous_locations_are_kept():
    st = LocalStorage({})
    assert st.areas["videos"] == storage_mod.PROJECT_ROOT / "uploads" / "videos"
    assert st.areas["queue"] == storage_mod.BACKEND_DIR / "uploads" / "queue"


# ---------- keys ----------

def test_new_files_are_stored_as_keys_not_paths(store):
    path = _put(store, "videos", "123_interview.mp4")
    key = store.to_stored(path)
    assert key == "videos/123_interview.mp4" and ":" not in key and "\\\\" not in key
    assert store.resolve(key) == path


def test_the_same_key_works_in_another_clone(tmp_path):
    """A different project folder or user name: same database value, different computer."""
    a = LocalStorage({"FMCG_STORAGE_ROOT": str(tmp_path / "windows_itone" / "uploads")})
    b = LocalStorage({"FMCG_STORAGE_ROOT": str(tmp_path / "mac_bhavesh" / "projects" / "uploads")})
    for st in (a, b):
        _put(st, "videos", "7_x.mp4")
    assert a.resolve("videos/7_x.mp4").parent == a.areas["videos"]
    assert b.resolve("videos/7_x.mp4").parent == b.areas["videos"]


# ---------- older absolute paths ----------

@pytest.mark.parametrize("old", [
    r"C:\Users\itone\FMCG_Learning_Engine\uploads\videos\1_a.mp4",          # Windows, user itone
    r"C:\Users\iONE IT\Documents\FMCG-Learning-Engine\uploads\videos\1_a.mp4",  # another user, spaces
    r"D:\Projects\fmcg\uploads\videos\1_a.mp4",                               # another drive and folder
    "/Users/bhaveshmishra/FMCG-Learning-Engine-clean/uploads/videos/1_a.mp4",  # macOS
    "/home/ubuntu/app/uploads/videos/1_a.mp4",                                 # a Linux / AWS server
])
def test_older_paths_from_any_computer_are_found_here(store, old):
    path = _put(store, "videos", "1_a.mp4")
    assert store.resolve(old) == path


def test_an_older_audio_path_finds_the_audio_folder(store):
    path = _put(store, "audio", "3_c.m4a")
    assert store.resolve(r"C:\Users\someone\proj\uploads\audio\3_c.m4a") == path


def test_an_older_path_from_a_custom_folder_is_found_by_name(store):
    path = _put(store, "videos", "9_custom.mp3")
    assert store.resolve(r"E:\fmcg\recordings\9_custom.mp3") == path


# ---------- safety ----------

@pytest.mark.parametrize("bad", ["videos/../../etc/hostname", "../secret.txt", "videos/..", "queue/../videos/x",
                                 "/etc/hostname", r"C:\Windows\System32\drivers\etc\hosts", "", None])
def test_nothing_outside_the_storage_folders_is_returned(store, bad):
    assert store.resolve(bad) is None


def test_missing_files_return_none(store):
    assert store.resolve("videos/never_saved.mp4") is None
    assert store.resolve(r"C:\Users\itone\FMCG\uploads\videos\never_saved.mp4") is None


# ---------- API workflows ----------

def test_a_saved_recording_stores_a_key_and_plays(client, db, alice, product, store):
    from app.api.video import save_feedback_record

    rid = start_survey(client, alice, product)["response_id"]
    path = _put(store, "videos", f"{rid}_interview.m4a", b"fake media")
    fid = save_feedback_record("interview.m4a", str(path), 30, 1, 4.0, "Fine.", "Fine.", [], response_id=rid)
    db.expire_all()
    assert db.get(FeedbackRecord, fid).video_path == f"videos/{rid}_interview.m4a", "no machine path in MySQL"
    got = client.get(f"/feedback/{fid}/video", headers=auth(alice))
    assert got.status_code == 200 and got.content == b"fake media"
    shown = client.get(f"/feedback/{fid}", headers=auth(alice)).json()["video_path"]
    assert shown == f"videos/{rid}_interview.m4a" and str(store.areas["videos"]) not in shown, "server path not exposed"


def test_an_older_windows_recording_still_plays(client, db, alice, product, store):
    rid = start_survey(client, alice, product)["response_id"]
    _put(store, "videos", f"{rid}_old.mp3", b"old media")
    rec = FeedbackRecord(original_filename="old.mp3", video_path=rf"C:\Users\itone\FMCG_Learning_Engine\uploads\videos\{rid}_old.mp3",
                         duration_seconds=10.0, response_id=rid)
    db.add(rec)
    db.commit()
    got = client.get(f"/feedback/{rec.id}/video", headers=auth(alice))
    assert got.status_code == 200 and got.content == b"old media"
    assert client.get(f"/feedback/{rec.id}", headers=auth(alice)).json()["video_path"] == f"videos/{rid}_old.mp3"


def test_queued_uploads_store_a_key(client, db, alice, product, store):
    rid = start_survey(client, alice, product)["response_id"]
    r = client.post("/analysis/jobs", headers=auth(alice), files={"file": ("voice.m4a", b"queued media", "audio/mp4")},
                    data={"response_id": str(rid)})
    assert r.status_code == 202, r.text
    job = db.query(AnalysisJob).filter(AnalysisJob.response_id == rid).order_by(AnalysisJob.id.desc()).first()
    assert job.stored_path.startswith("queue/") and store.resolve(job.stored_path).read_bytes() == b"queued media"


def test_recipe_attachments_store_a_key(client, db, admin, product, store):
    org = make_user(db, role="client")
    up = client.post(f"/products/{product.id}/recipe/attachments", headers=auth(org), files={"file": ("sop.pdf", b"%PDF x", "application/pdf")})
    assert up.status_code == 201
    row = db.get(RecipeAttachment, up.json()["id"])
    assert row.stored_path.startswith("recipe_files/") and ":" not in row.stored_path
    assert client.get(f"/recipe-attachments/{row.id}/file", headers=auth(admin)).content == b"%PDF x"


# ---------- converting older rows ----------

def test_the_conversion_tool_is_safe(client, db, alice, product, store, tmp_path):
    from app.tools import storage_keys

    rid = start_survey(client, alice, product)["response_id"]
    _put(store, "videos", f"{rid}_found.mp3")
    found = FeedbackRecord(original_filename="f.mp3", video_path=rf"C:\Users\itone\X\uploads\videos\{rid}_found.mp3", duration_seconds=1.0, response_id=rid)
    missing = FeedbackRecord(original_filename="m.mp3", video_path=r"C:\Users\itone\X\uploads\videos\gone_forever.mp3", duration_seconds=1.0, response_id=rid)
    db.add_all([found, missing])
    db.commit()
    before = (found.video_path, missing.video_path)
    plan = storage_keys.plan(db)["feedback_records"]
    assert (found.id, before[0], f"videos/{rid}_found.mp3") in plan["convert"] and (missing.id, before[1]) in plan["missing"]
    db.expire_all()
    assert (db.get(FeedbackRecord, found.id).video_path, db.get(FeedbackRecord, missing.id).video_path) == before, "dry run changes nothing"
    backup = tmp_path / "backup.csv"
    assert storage_keys.apply(db, storage_keys.plan(db), backup) >= 1
    db.expire_all()
    assert db.get(FeedbackRecord, found.id).video_path == f"videos/{rid}_found.mp3"
    assert db.get(FeedbackRecord, missing.id).video_path == before[1], "a file not found here is left unchanged"
    rows = list(csv.DictReader(backup.open(encoding="utf-8")))
    assert any(r["id"] == str(found.id) and r["old_value"] == before[0] for r in rows)
    assert storage_keys.plan(db)["feedback_records"]["convert"] == [] or all(
        i != found.id for i, _, _ in storage_keys.plan(db)["feedback_records"]["convert"]), "running again changes nothing"
