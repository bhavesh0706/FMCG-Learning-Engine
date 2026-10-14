// Frontend configuration: the ONE place that knows where the backend is.
//
// VITE_API_BASE_URL comes from frontend/.env.local (development) or
// .env.<mode>.local / the build environment (staging, production).
// vite.config.js checks it before the dev server starts or a build runs.
//
// Only VITE_* values reach this code, and they end up inside the JavaScript
// sent to every browser: never put passwords, keys or tokens in them.

const raw = import.meta.env.VITE_API_BASE_URL;

if (!raw) {
  throw new Error(
    "VITE_API_BASE_URL is not set. Add it to frontend/.env.local and restart `npm run dev`."
  );
}

// Without a trailing slash, so `${API_BASE_URL}/feedback/1` is always correct.
export const API_BASE_URL = raw.trim().replace(/\/+$/, "");