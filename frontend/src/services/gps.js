/*
  GPS in the browser (web app), the same data as the Android and iOS apps
  (mobile/lib/services/gps_service.dart):

    { latitude, longitude, accuracy (m), captured_at (ISO, UTC), source: "web" }

  The Geolocation API doesn't say HOW the position was found: a phone browser usually
  uses the phone's GPS (works offline), a laptop usually positions by Wi-Fi/IP, which
  needs a network. So without internet a fix may be unavailable on a computer; the
  message says so. Geolocation also needs a secure page (https or localhost).

  A fix is kept in this browser (localStorage) until the survey is saved, so a lost
  connection or a reload doesn't lose it, and it is sent with its original time.
*/

export const POOR_ACCURACY_M = 100;
const KEPT_KEY = "fmcg.surveyGps";
const KEEP_FOR_MS = 6 * 60 * 60 * 1000; // a kept fix older than this is dropped

export class GpsError extends Error {
  constructor(problem, message) {
    super(message);
    this.problem = problem; // unsupported | insecure | denied | unavailable | timeout
  }
}

const offline = () => typeof navigator !== "undefined" && navigator.onLine === false;

const messages = {
  unsupported: "This browser can't share its location. Type the location.",
  insecure: "Location works only on a secure page (https). Type the location, or open the app over https.",
  denied: "Location access is blocked for this site. Allow it in the browser's site settings (and the device's location settings), or type the location.",
  timeout: "No position in time. Move near a window or outside and try again, or type the location.",
  unavailable: () => offline()
    ? "No position: without internet, this browser can only use the device's GPS, and none is available here (computers usually locate by Wi-Fi). Type the location, or use the mobile app."
    : "Location is turned off or unavailable on this device. Turn it on, or type the location.",
};

const message = (problem) => (typeof messages[problem] === "function" ? messages[problem]() : messages[problem]);

function toFix(pos) {
  return {
    latitude: Number(pos.coords.latitude.toFixed(6)),
    longitude: Number(pos.coords.longitude.toFixed(6)),
    accuracy: Math.round(pos.coords.accuracy * 10) / 10,
    // The moment the browser took the position (not when the survey is saved).
    captured_at: new Date(pos.timestamp || Date.now()).toISOString(),
    source: "web",
  };
}

function once(options) {
  return new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve, reject, options));
}

/** One fix. Tries a fresh high-accuracy position first, then any recent one. Throws GpsError. */
export async function captureFix({ timeout = 20000 } = {}) {
  if (typeof navigator === "undefined" || !navigator.geolocation) throw new GpsError("unsupported", message("unsupported"));
  if (typeof window !== "undefined" && window.isSecureContext === false) throw new GpsError("insecure", message("insecure"));
  try {
    return toFix(await once({ enableHighAccuracy: true, timeout, maximumAge: 0 }));
  } catch (first) {
    if (first?.code === 1) throw new GpsError("denied", message("denied"));
    // No fresh fix in time: a position the browser took in the last 5 minutes.
    try {
      return { ...toFix(await once({ enableHighAccuracy: false, timeout: 8000, maximumAge: 5 * 60 * 1000 })), recent: true };
    } catch (second) {
      if (second?.code === 1) throw new GpsError("denied", message("denied"));
      const problem = first?.code === 3 ? "timeout" : "unavailable";
      throw new GpsError(problem, message(problem));
    }
  }
}

/** Fields of POST /surveys/responses for a fix (or none). */
export function fixFields(fix) {
  if (!fix) return { latitude: null, longitude: null };
  return {
    latitude: fix.latitude,
    longitude: fix.longitude,
    location_accuracy: fix.accuracy ?? null,
    location_captured_at: fix.captured_at ?? null,
    location_source: fix.source || "web",
  };
}

/** "28.474388, 77.503990 · ±7 m · 10:42" */
export function describeFix(fix) {
  if (!fix) return "";
  const time = fix.captured_at
    ? new Date(fix.captured_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "";
  return `${fix.latitude.toFixed(6)}, ${fix.longitude.toFixed(6)} · ±${Math.round(fix.accuracy ?? 0)} m${time ? ` · ${time}` : ""}`;
}

export const isPoor = (fix) => Boolean(fix && fix.accuracy > POOR_ACCURACY_M);

/* Offline there's no address: a place field gets the position instead,
   "GPS 28.601077, 77.432895 (±6 m)". It becomes the address when back online, or when the
   survey reaches the server (backend api/geo.py is_gps_placeholder; same text as the apps). */
export const placeholderFor = (fix) => `GPS ${fix.latitude.toFixed(6)}, ${fix.longitude.toFixed(6)} (±${Math.round(fix.accuracy ?? 0)} m)`;
export const isPlaceholder = (text) => /^\s*GPS\s+-?\d{1,2}(\.\d+)?,\s*-?\d{1,3}(\.\d+)?/.test(text || "");

/* ---------- Kept in this browser until the survey is saved ---------- */

export function keepFix(fix) {
  try {
    if (fix) localStorage.setItem(KEPT_KEY, JSON.stringify(fix));
    else localStorage.removeItem(KEPT_KEY);
  } catch {
    /* storage unavailable: the fix stays in the page only */
  }
}

export function keptFix() {
  try {
    const fix = JSON.parse(localStorage.getItem(KEPT_KEY) || "null");
    if (!fix || typeof fix.latitude !== "number" || typeof fix.longitude !== "number") return null;
    const age = Date.now() - new Date(fix.captured_at || 0).getTime();
    return age >= 0 && age <= KEEP_FOR_MS ? fix : null;
  } catch {
    return null;
  }
}
