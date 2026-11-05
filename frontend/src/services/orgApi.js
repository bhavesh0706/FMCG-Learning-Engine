import axios from "axios";
import { API_BASE_URL } from "./feedbackApi";

// Organization portal (backend: app/api/org.py; recipes: app/api/recipes.py).
const api = axios.create({ baseURL: API_BASE_URL });
const data = (p) => p.then((r) => r.data);

// Readable message from any API error.
export function orgError(err, fallback = "Something went wrong. Try again.") {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && typeof d.message === "string") return d.message;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg).replace("Value error, ", "");
  if (err?.code === "ERR_NETWORK") return `Can't reach the server at ${API_BASE_URL}.`;
  return fallback;
}

// Recipe standards that blocked a round (HTTP 409, code recipe_standards_not_met), or [].
export function standardsFailures(err) {
  const d = err?.response?.data?.detail;
  return d && d.code === "recipe_standards_not_met" && Array.isArray(d.failed_standards) ? d.failed_standards : [];
}

export const getOverview = () => data(api.get("/org/overview"));
export const getOrgProducts = () => data(api.get("/org/products"));
export const getOrgProduct = (id) => data(api.get(`/org/products/${id}`));
export const getRoundReport = (id, round = null) =>
  data(api.get(`/org/products/${id}/report`, { params: round != null ? { round } : {} }));
export const compareRounds = (id, a = null, b = null) =>
  data(api.get(`/org/products/${id}/compare`, { params: { ...(a ? { a } : {}), ...(b ? { b } : {}) } }));
export const startRound = (id, body) => data(api.post(`/org/products/${id}/rounds`, body));
export const endRound = (id, number) => data(api.post(`/org/products/${id}/rounds/${number}/end`));
export const getUpdates = (limit = 100) => data(api.get("/org/updates", { params: { limit } }));
export const getUnreadUpdates = () => data(api.get("/org/updates/unread"));
export const markUpdatesSeen = () => data(api.post("/org/updates/seen"));
export const getOrgAccount = () => data(api.get("/org/account"));
