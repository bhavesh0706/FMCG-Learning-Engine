import axios from "axios";
import { API_BASE_URL } from "./feedbackApi";

// Team management (backend: app/api/admin.py).
const api = axios.create({ baseURL: API_BASE_URL });

const tzOffset = () => -new Date().getTimezoneOffset(); // minutes ahead of UTC (India = 330)

// GET /admin/users -> { users: [{ id, name, mobile, email, role, is_active, can_view_history, last_login_at, stats }] }
export async function getTeam() {
  const res = await api.get("/admin/users", { params: { tz_offset: tzOffset() } });
  return res.data;
}

// PUT /admin/users/{id} { can_view_history?, is_active?, role? } -> { user }
export async function updateTeamMember(id, changes) {
  const res = await api.put(`/admin/users/${id}`, changes);
  return res.data;
}

// POST /admin/users -> { user, linked_surveys } the admin creates a member's login
export async function addTeamMember(member) {
  const res = await api.post("/admin/users", member);
  return res.data;
}

// PUT /admin/users/{id}/password
export async function resetMemberPassword(id, password) {
  const res = await api.put(`/admin/users/${id}/password`, { password });
  return res.data;
}

// Products an executive may survey (empty = every product that isn't assigned to someone else)
export async function getMemberProducts(id) {
  const res = await api.get(`/admin/users/${id}/products`);
  return res.data;
}

export async function saveMemberProducts(id, productIds) {
  const res = await api.put(`/admin/users/${id}/products`, { product_ids: productIds });
  window.dispatchEvent(new Event("fmcg:catalog-changed"));
  window.dispatchEvent(new Event("fmcg:data-changed"));
  return res.data;
}
// Sign a member out of every device (lost phone); their password stays the same.
export async function signOutMember(id) {
  const res = await api.post(`/admin/users/${id}/sign-out`);
  return res.data;
}

// GET /admin/audit -> { total, limit, offset, entries: [{ id, at, user_name, user_role, action, summary, details, ip, ... }] }
// Filtered and paged on the server: { limit, offset, q, action ("user." = every team change), days, user_id }
export async function getAuditLog(params = {}) {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined));
  const res = await api.get("/admin/audit", { params: clean });
  return res.data;
}
