import axios from "axios";
import { API_BASE_URL } from "../config";

/*
  Web session: who is signed in, and the login token on every API request.

  This file must be the FIRST import in App.jsx: it equips axios before any
  other API file creates its client, so feedbackApi.js, teamApi.js, etc. all
  send the token without changes. If the server says the session is no longer
  valid (expired, or the account was disabled), the app returns to sign-in.
*/

/*
  The session lives in sessionStorage: it belongs to ONE browser tab.
    - Two tabs can be signed in as two different people (an admin and an executive)
      and stay that way through refreshes; they never take over each other.
    - Opening the app in a new tab or window always asks to sign in.
  Only "recently used accounts" (name, role, mobile/email: never passwords) are kept
  on the device, to make signing in quicker.
*/
const KEY = "fmcg.session";
const RECENT_KEY = "fmcg.recentAccounts";
const listeners = new Set();
let session = load();
let signOutReason = "";

function load() {
  try {
    // One-time clean-up: older versions kept the session for every tab.
    localStorage.removeItem(KEY);
  } catch {
    // ignore
  }
  try {
    const s = JSON.parse(sessionStorage.getItem(KEY) || "null");
    return s?.token && s?.user ? s : null;
  } catch {
    return null;
  }
}

function save(next) {
  session = next;
  try {
    if (next) sessionStorage.setItem(KEY, JSON.stringify(next));
    else sessionStorage.removeItem(KEY);
  } catch {
    // storage blocked: the session lasts until the page is reloaded
  }
  if (next?.user) rememberAccount(next.user);
  listeners.forEach((fn) => fn(session));
}

/* ---------- Recently used accounts on this device (no passwords) ---------- */

export function recentAccounts() {
  try {
    const list = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(list) ? list.slice(0, 6) : [];
  } catch {
    return [];
  }
}

function rememberAccount(user) {
  const identifier = user.mobile || user.email;
  if (!identifier) return;
  const entry = { identifier, name: user.name, role: user.role === "admin" ? "Admin" : "Survey person" };
  try {
    const rest = recentAccounts().filter((a) => a.identifier !== identifier);
    localStorage.setItem(RECENT_KEY, JSON.stringify([entry, ...rest].slice(0, 6)));
  } catch {
    // ignore
  }
}

export function forgetAccount(identifier) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(recentAccounts().filter((a) => a.identifier !== identifier)));
  } catch {
    // ignore
  }
}

export const getSession = () => session;
export const getToken = () => session?.token || null;
export const isAdmin = (s = session) => s?.user?.role === "admin";

export function onSessionChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Why the last session ended (shown once on the sign-in page). */
export function takeSignOutReason() {
  const r = signOutReason;
  signOutReason = "";
  return r;
}

export function signOut(reason = "") {
  signOutReason = reason;
  save(null);
}

/* ---------- Token on every request, from every axios client ---------- */

function equip(instance) {
  instance.interceptors.request.use((config) => {
    const token = getToken();
    if (token) {
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });
  instance.interceptors.response.use(
    (res) => res,
    (err) => {
      const url = String(err?.config?.url || "");
      if (err?.response?.status === 401 && getToken() && !url.includes("/auth/login") && !url.includes("/auth/setup")) {
        signOut("Your session ended or your account was changed. Please sign in again.");
      }
      return Promise.reject(err);
    }
  );
  return instance;
}

equip(axios);
const originalCreate = axios.create.bind(axios);
axios.create = (...args) => equip(originalCreate(...args));

/** Media (audio/video elements) can't send headers: add the token to the address. */
export function withToken(url) {
  const token = getToken();
  if (!url || !token) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}

/* ---------- Sign-in API (base URL passed in, to avoid importing other API files here) ---------- */

function message(err, fallback) {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg).replace("Value error, ", "");
  if (err?.code === "ERR_NETWORK") return `Can't reach the server at ${API_BASE_URL}. Check that the backend is running.`;
  return fallback;
}

export async function getSetupStatus(base) {
  const res = await axios.get(`${base}/auth/setup-status`);
  return res.data;
}

export async function signIn(base, identifier, password) {
  try {
    const res = await axios.post(`${base}/auth/login`, { identifier: identifier.trim(), password });
    save({ token: res.data.token, user: res.data.user });
    return res.data.user;
  } catch (err) {
    throw new Error(message(err, "Sign-in failed. Try again."), { cause: err });
  }
}

export async function createFirstAdmin(base, name, identifier, password) {
  try {
    const res = await axios.post(`${base}/auth/setup`, { name: name.trim(), identifier: identifier.trim(), password });
    save({ token: res.data.token, user: res.data.user });
    return res.data.user;
  } catch (err) {
    throw new Error(message(err, "The admin account couldn't be created."), { cause: err });
  }
}

/** Latest name / role / permissions from the server (an admin may have changed them). */
export async function refreshMe(base) {
  if (!getToken()) return null;
  try {
    const res = await axios.get(`${base}/auth/me`);
    save({ token: getToken(), user: res.data.user });
    return res.data.user;
  } catch {
    return session?.user || null; // offline: keep what we have; 401 already signs out
  }
}