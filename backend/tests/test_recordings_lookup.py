"""
Recordings keep playing after the data moves to another computer: feedback_records.
video_path holds the full path from the computer that received the upload (for example
a Windows path), and app.services.recordings finds the file in this computer's uploads.
"""

from datetime import datetime

import pytest
from conftest import auth, make_user

from app.database.models import FeedbackRecord
from app.services import recordings


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    (root / "videos").mkdir(parents=True)
    (root / "audio").mkdir(parents=True)
    monkeypatch.setenv("FMCG_RECORDINGS_DIR", str(root / "videos"))
    return root


def _record(db, path):
    rec = FeedbackRecord(original_filename="Bengali.mp3", video_path=path, duration_seconds=42,
                         uploaded_at=datetime.utcnow(), overall_summary="ok", overall_sentiment="positive", overall_rating=5.0)
    db.add(rec)
    db.commit()
    return rec


def test_the_stored_path_is_used_when_it_exists(uploads):
    f = uploads / "videos" / "1_here.mp3"
    f.write_bytes(b"ID3")
    assert recordings.resolve_recording(str(f)) == f


def test_a_windows_path_is_found_in_this_uploads_folder(uploads):
    f = uploads / "videos" / "30_Bengali.mp3"
    f.write_bytes(b"ID3")
    stored = r"C:\Users\iONE IT\Documents\FMCG-Learning-Engine\uploads\videos\30_Bengali.mp3"
    assert recordings.resolve_recording(stored) == f


def test_a_path_from_another_folder_is_found_by_name(uploads):
    f = uploads / "audio" / "7_voice.m4a"
    f.write_bytes(b"....")
    assert recordings.resolve_recording("/old/place/recordings/7_voice.m4a") == f


def test_missing_files_and_paths_outside_uploads_are_not_returned(uploads, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    assert recordings.resolve_recording(r"C:\elsewhere\uploads\videos\nope.mp3") is None
    assert recordings.resolve_recording(r"C:\x\uploads\..\..\secret.txt") is None
    assert recordings.resolve_recording(None) is None and recordings.resolve_recording("") is None


def test_the_app_streams_a_recording_stored_with_a_windows_path(client, db, uploads):
    admin = make_user(db, role="admin")
    (uploads / "videos" / "30_Bengali.mp3").write_bytes(b"ID3fake-mp3")
    rec = _record(db, r"C:\Users\iONE IT\Documents\FMCG-Learning-Engine\uploads\videos\30_Bengali.mp3")
    r = client.get(f"/feedback/{rec.id}/video", headers=auth(admin))
    assert r.status_code == 200 and r.content == b"ID3fake-mp3"
    assert r.headers["content-type"].startswith("audio/mpeg")
    gone = _record(db, r"C:\Users\iONE IT\Documents\FMCG-Learning-Engine\uploads\videos\31_missing.mp3")
    assert client.get(f"/feedback/{gone.id}/video", headers=auth(admin)).status_code == 404


def test_check_recordings_tool_reports_ok_found_and_missing(db, uploads, capsys):
    from app.tools import check_recordings

    here = uploads / "videos" / "1_here.mp3"
    here.write_bytes(b"x")
    (uploads / "videos" / "2_moved.mp3").write_bytes(b"x")
    _record(db, str(here))
    _record(db, r"C:\old\uploads\videos\2_moved.mp3")
    _record(db, r"C:\old\uploads\videos\3_gone.mp3")
    check_recordings.main()
    out = capsys.readouterr().out
    assert "FOUND" in out and "MISSING" in out and "3_gone.mp3" in out