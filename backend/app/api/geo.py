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
import threading
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


def place_from(payload: dict) -> dict:
    """A short, readable place from a Nominatim reverse result."""
    a = payload.get("address") or {}
    locality = _first(a, "suburb", "neighbourhood", "quarter", "residential", "village", "hamlet", "city_district")
    city = _first(a, "city", "town", "municipality", "county", "state_district")
    state = _first(a, "state", "region")
    country = _first(a, "country")
    parts = []
    for p in (locality, city, state):
        if p and p not in parts:
            parts.append(p)
    label = ", ".join(parts) or ", ".join((payload.get("display_name") or "").split(", ")[:3])
    return {"label": label, "locality": locality, "city": city, "state": state, "country": country}


def _lookup(lat: float, lng: float) -> dict:
    url = os.environ.get("FMCG_GEOCODER_URL") or DEFAULT_URL
    contact = os.environ.get("FMCG_GEOCODER_CONTACT", "")
    query = urllib.parse.urlencode({"format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lng:.6f}", "zoom": 16, "addressdetails": 1})
    req = urllib.request.Request(f"{url}?{query}", headers={
        "User-Agent": f"FMCG-Learning-Engine/1.0{(' (' + contact + ')') if contact else ''}",
        "Accept-Language": "en",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 (fixed https provider or admin-set URL)
        return json.loads(resp.read().decode("utf-8"))


@router.get("/reverse")
def reverse(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180), _user=Depends(current_user)):
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
        hit = place_from(payload)
        if not hit["label"]:
            raise HTTPException(status_code=404, detail="No address found for this position. Type the location.")
        with _LOCK:
            _CACHE[key] = hit
            while len(_CACHE) > _CACHE_MAX:
                _CACHE.popitem(last=False)
    return {**hit, "latitude": lat, "longitude": lng}