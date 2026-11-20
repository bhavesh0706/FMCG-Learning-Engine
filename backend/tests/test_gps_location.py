"""
Offline GPS: a survey's GPS fix keeps its accuracy, its original capture time (taken
offline, sent later) and the platform (android | ios | web), the same on every client.
"""

from datetime import datetime, timedelta

import pytest
from conftest import assign, auth, open_round

from app.database.models import ProductAssignment, SurveyResponse


@pytest.fixture(autouse=True)
def _live_survey(db, product):
    open_round(db, product)  # surveys are taken in a live survey (round)


def _respondent(client, person):
    r = client.post("/surveys/respondents", headers=auth(person),
                    json={"name": "Customer", "consent_given": True, "location": "Sector 18"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create(client, db, person, product, **gps):
    if not db.query(ProductAssignment).filter_by(product_id=product.id, user_id=person.id).count():
        assign(db, product, person)
    body = {"product_id": product.id, "respondent_id": _respondent(client, person), **gps}
    return client.post("/surveys/responses", headers=auth(person), json=body)


def test_fix_taken_offline_keeps_accuracy_time_and_platform(client, db, alice, product):
    taken = (datetime.utcnow() - timedelta(hours=5)).replace(microsecond=0)  # captured offline, synced later
    r = _create(client, db, alice, product, latitude=28.474388, longitude=77.503990, location_accuracy=12.345,
                location_captured_at=taken.isoformat() + "Z", location_source="android")
    assert r.status_code == 201, r.text
    row = db.get(SurveyResponse, r.json()["id"])
    assert (row.latitude, row.longitude, row.location_accuracy) == (28.474388, 77.50399, 12.3)
    assert row.location_captured_at == taken, "the original capture time, not the upload time"
    assert row.location_source == "android"
    out = client.get(f"/surveys/responses/{row.id}", headers=auth(alice)).json()
    assert out["location_captured_at"] == taken.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert out["location_accuracy"] == 12.3 and out["location_source"] == "android"


def test_capture_time_with_a_timezone_is_stored_in_utc(client, db, alice, product):
    r = _create(client, db, alice, product, latitude=19.07, longitude=72.87, location_accuracy=30,
                location_captured_at="2026-11-19T10:30:00+05:30", location_source="ios")
    assert r.status_code == 201, r.text
    row = db.get(SurveyResponse, r.json()["id"])
    assert row.location_captured_at == datetime(2026, 11, 19, 5, 0) and row.location_source == "ios"


def test_bad_clock_or_unknown_platform_never_blocks_the_survey(client, db, alice, product):
    future = (datetime.utcnow() + timedelta(days=2)).isoformat() + "Z"
    r = _create(client, db, alice, product, latitude=12.97, longitude=77.59,
                location_captured_at=future, location_source="windows-phone")
    assert r.status_code == 201, r.text
    row = db.get(SurveyResponse, r.json()["id"])
    assert row.latitude == 12.97 and row.location_captured_at is None and row.location_source is None


def test_coordinates_come_together_and_details_need_a_position(client, db, alice, product):
    assert _create(client, db, alice, product, latitude=12.97).status_code == 422
    assert _create(client, db, alice, product, latitude=12.97, longitude=77.59,
                   location_accuracy=-1).status_code == 422
    r = _create(client, db, alice, product, location_accuracy=8, location_source="web",
                location_captured_at=datetime.utcnow().isoformat() + "Z")
    assert r.status_code == 201
    row = db.get(SurveyResponse, r.json()["id"])
    assert (row.latitude, row.location_accuracy, row.location_captured_at, row.location_source) == (None, None, None, None)


def test_older_apps_without_the_new_fields_still_work(client, db, alice, product):
    r = _create(client, db, alice, product, latitude=28.6, longitude=77.2)
    assert r.status_code == 201
    row = db.get(SurveyResponse, r.json()["id"])
    assert row.latitude == 28.6 and row.location_accuracy is None and row.location_captured_at is None


def test_gps_placeholders_of_an_offline_survey_become_the_address_on_upload(client, db, alice, product, monkeypatch):
    """Taken offline: the area and survey location hold "GPS lat, lng (±m)"; the server fills
    in the address once the survey arrives. Typed places are never replaced."""
    import app.api.geo as geo
    from app.database.models import Respondent

    seen = []

    def fake_reverse(lat, lng):
        seen.append((lat, lng))
        return {"label": "Sector 62, Noida, Uttar Pradesh", "locality": "Sector 62", "city": "Noida",
                "state": "Uttar Pradesh", "country": "India"}

    monkeypatch.setattr(geo, "reverse_place", fake_reverse)
    assign(db, product, alice)
    rid = client.post("/surveys/respondents", headers=auth(alice), json={
        "name": "Customer", "consent_given": True, "location": "GPS 28.601077, 77.432895 (±6 m)",
        "country": "India", "state": "Uttar Pradesh", "city": "Greater Noida"}).json()["id"]
    r = client.post("/surveys/responses", headers=auth(alice), json={
        "product_id": product.id, "respondent_id": rid, "location": "GPS 28.601077, 77.432895 (±6 m)",
        "latitude": 28.601077, "longitude": 77.432895, "location_accuracy": 6, "location_source": "ios"})
    assert r.status_code == 201, r.text
    db.expire_all()
    row, who = db.get(SurveyResponse, r.json()["id"]), db.get(Respondent, rid)
    assert seen == [(28.601077, 77.432895)]
    assert row.location == "Sector 62, Noida, Uttar Pradesh" and who.location == "Sector 62"
    assert who.city == "Greater Noida", "a city chosen by the survey person is kept"

    # Typed by hand: nothing is looked up.
    rid2 = client.post("/surveys/respondents", headers=auth(alice), json={
        "name": "Other", "consent_given": True, "location": "Market road"}).json()["id"]
    client.post("/surveys/responses", headers=auth(alice), json={
        "product_id": product.id, "respondent_id": rid2, "location": "D-Mart", "latitude": 28.6, "longitude": 77.4})
    assert len(seen) == 1
