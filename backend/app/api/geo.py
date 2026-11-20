"""
Turning the phone's GPS position into a readable place for "Survey location".

GET /geo/reverse?lat=28.6011&lng=77.4327  (any signed-in account; the Super Admin's
portal can't open it, see access.py)
  -> {"label": "Sector 62, Noida, Uttar Pradesh", "locality": ..., "city": ...,
      "state": ..., "country": ..., "latitude": ..., "longitude": ...}

The web app and the mobile app both call this, so Android, iOS and the browser give
the same result and the apps need no geocoding package. The provider is
OpenStreetMap Nominatim by default (no key); its usage policy asks for an
identifying User-Agent and few requests, so results are cached per ~100 m.
Settings (environment):
  FMCG_GEOCODER=off             turn it off (the apps fall back to typing)
  FMCG_GEOCODER_URL=...         another Nominatim-compatible reverse endpoint
  FMCG_GEOCODER_CONTACT=...     contact e-mail sent in the User-Agent
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.auth import current_user

router = APIRouter(prefix="/geo", tags=["Location"])

DEFAULT_URL = "https://nominatim.openstreetmap.org/reverse"
_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_MAX = 2000
_LOCK = threading.Lock()


def _first(address: dict, *keys) -> str:
    for k in keys:
        v = (address.get(k) or "").strip()
        if v:
            return v
    return ""
# "Pune District", "Chhindwara Tahsil", "Pune City Subdistrict" -> the place's own name
_ADMIN_WORDS = re.compile(r"\s+(district|sub-?district|tahsil|tehsil|taluka|taluk|mandal|block|division)$", re.I)

# Address parts that can name the town or city, most specific first. In India OpenStreetMap
# often has no "city" (e.g. Ahmedabad gives only state_district); "county" is usually a
# taluka ("SabarmatiTaluka"), so it comes last.
_CITY_KEYS = ("city", "town", "municipality", "state_district", "city_district", "village", "county")


def _plain(name: str) -> str:
    return _ADMIN_WORDS.sub("", name).strip()


# Address parts that name the area / locality inside the city, most specific first.
_LOCALITY_KEYS = ("suburb", "neighbourhood", "quarter", "residential", "hamlet", "village", "city_district")


def has_locality(payload: dict) -> bool:
    a = payload.get("address") or {}
    return any((a.get(k) or "").strip() for k in _LOCALITY_KEYS)


def merge(detailed: dict, wider: dict) -> dict:
    """The detailed answer, with address parts only the wider answer has (e.g. the suburb)."""
    return {**detailed, "address": {**(wider.get("address") or {}), **(detailed.get("address") or {})}}




def place_from(payload: dict) -> dict:
    """A short, readable place from a Nominatim reverse result."""
    a = payload.get("address") or {}
    city_from = next((k for k in _CITY_KEYS if (a.get(k) or "").strip()), "")
    city = _plain(a.get(city_from) or "") if city_from else ""
    # The area inside the city. Never the city or district itself: when OpenStreetMap has no
    # locality for the spot, the road, or the sub-district with the PIN code ("Dadri, 201318").
    locality = next((a[k].strip() for k in _LOCALITY_KEYS if (a.get(k) or "").strip() and _plain(a[k]) != city), "")
    if not locality:
        road = (a.get("road") or "").strip()
        county = _plain(a.get("county") or "")
        postcode = (a.get("postcode") or "").strip()
        locality = road or ", ".join(p for p in (county if county != city else "", postcode) if p)
    state = _first(a, "state", "region")
    country = _first(a, "country")
    parts = []
    for p in (locality, city, state):
        if p and p not in parts:
            parts.append(p)
    label = ", ".join(parts) or ", ".join((payload.get("display_name") or "").split(", ")[:3])
    # Every name that may be the city, so the apps can find the one in their city list
    # (the city list of the state may know the district's name but not the town's).
    places = []
    for k in _CITY_KEYS + ("suburb",):
        for name in (a.get(k), _plain(a.get(k) or "")):
            name = (name or "").strip()
            if name and name not in places:
                places.append(name)
    # city_from: which part the city came from. "state_district" / "county" = OpenStreetMap
    # knows no town here, so the apps say the city was taken from the district.
    return {"label": label, "locality": locality, "city": city, "city_from": city_from, "state": state,
            "country": country, "places": places}


def _lookup(lat: float, lng: float, zoom: int = 16) -> dict:
    url = os.environ.get("FMCG_GEOCODER_URL") or DEFAULT_URL
    contact = os.environ.get("FMCG_GEOCODER_CONTACT", "")
    query = urllib.parse.urlencode({"format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lng:.6f}", "zoom": zoom, "addressdetails": 1})
    req = urllib.request.Request(f"{url}?{query}", headers={
        "User-Agent": f"FMCG-Learning-Engine/1.0{(' (' + contact + ')') if contact else ''}",
        "Accept-Language": "en",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 (fixed https provider or admin-set URL)
        return json.loads(resp.read().decode("utf-8"))


@router.get("/reverse")
def reverse(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180), _user=Depends(current_user)):
    return reverse_place(lat, lng)


# A position typed into a place field by the apps while offline ("GPS 28.601077, 77.432895 (±6 m)"):
# the address isn't known yet. The server replaces it once the survey arrives (api/surveys.py).
GPS_PLACEHOLDER = re.compile(r"^\s*GPS\s+-?\d{1,2}(\.\d+)?,\s*-?\d{1,3}(\.\d+)?\b")


def is_gps_placeholder(text) -> bool:
    return bool(text) and bool(GPS_PLACEHOLDER.match(str(text)))


def reverse_place(lat: float, lng: float) -> dict:
    """The address of a position (cached ~100 m). Raises HTTPException 503/404 when unknown."""
    if os.environ.get("FMCG_GEOCODER", "").lower() in ("off", "0", "false", "no"):
        raise HTTPException(status_code=503, detail="Finding the address is turned off on this server. Type the location.")
    key = (round(lat, 3), round(lng, 3))  # ~100 m: nearby surveys reuse the answer
    with _LOCK:
        hit = _CACHE.get(key)
        if hit:
            _CACHE.move_to_end(key)
    if hit is None:
        try:
            payload = _lookup(lat, lng)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise HTTPException(status_code=503, detail="Couldn't find the address right now. Type the location, or try again.")
        if not isinstance(payload, dict) or payload.get("error"):
            raise HTTPException(status_code=404, detail="No address found for this position. Type the location.")
        if not has_locality(payload):
            # The spot is inside an unnamed area (e.g. a housing society in Greater Noida West):
            # one level wider names the suburb ("Gaur City 2"). Waits 1 s: the provider allows
            # one request per second. A failed second try keeps the first answer.
            time.sleep(1)
            try:
                wider = _lookup(lat, lng, zoom=14)
                if isinstance(wider, dict) and not wider.get("error"):
                    payload = merge(payload, wider)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                pass
        hit = place_from(payload)
        if not hit["label"]:
            raise HTTPException(status_code=404, detail="No address found for this position. Type the location.")
        with _LOCK:
            _CACHE[key] = hit
            while len(_CACHE) > _CACHE_MAX:
                _CACHE.popitem(last=False)
    return {**hit, "latitude": lat, "longitude": lng}
