import axios from "axios";
import { API_BASE_URL } from "./feedbackApi";
import { loadLov } from "./lov";

// Other Master Data (backend api/other_masters.py): master tables and their values.
const api = axios.create({ baseURL: API_BASE_URL });
const data = (p) => p.then((r) => r.data);
// A change also refreshes the lists the rest of the app reads (GET /lov).
const changed = (p) => data(p).then((d) => { loadLov(true); return d; });

export const listMasterTables = () => data(api.get("/admin/other-masters"));
export const createMasterTable = (body) => changed(api.post("/admin/other-masters", body));
export const updateMasterTable = (id, body) => changed(api.patch(`/admin/other-masters/${id}`, body));
export const deleteMasterTable = (id) => changed(api.delete(`/admin/other-masters/${id}`));
export const listMasterValues = (id) => data(api.get(`/admin/other-masters/${id}/values`));
export const addMasterValue = (id, body) => changed(api.post(`/admin/other-masters/${id}/values`, body));
export const updateMasterValue = (valueId, body) => changed(api.patch(`/admin/other-masters/values/${valueId}`, body));
export const deleteMasterValue = (valueId) => changed(api.delete(`/admin/other-masters/values/${valueId}`));

export function masterError(err, fallback = "Something went wrong. Try again.") {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return d[0].msg.replace(/^Value error, /, "");
  return fallback;
}
