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
export const compareRounds = (id, a = null, b = null, surveys = null) =>
  data(api.get(`/org/products/${id}/compare`, { params: { ...(a ? { a } : {}), ...(b ? { b } : {}), ...(surveys ? { surveys: surveys.join(",") } : {}) } }));
// What one survey used: its recipe version, standards checked against it, the questions it asked.
export const getRoundSnapshot = (id, number) => data(api.get(`/org/products/${id}/rounds/${number}/snapshot`));
export const startRound = (id, body) => data(api.post(`/org/products/${id}/rounds`, body));
export const endRound = (id, number) => data(api.post(`/org/products/${id}/rounds/${number}/end`));
// A survey (round) started by mistake: the latest one, while it has no responses
export const deleteRound = (id, number) => data(api.delete(`/org/products/${id}/rounds/${number}`));
export const getUpdates = (limit = 100) => data(api.get("/org/updates", { params: { limit } }));
export const getUnreadUpdates = () => data(api.get("/org/updates/unread"));
export const markUpdatesSeen = () => data(api.post("/org/updates/seen"));
export const getOrgAccount = () => data(api.get("/org/account"));

// Super Admin accounts (Account page). Every change needs the signed-in Super Admin's password.
export const listSuperAdmins = () => data(api.get("/org/super-admins"));
export const addSuperAdmin = (body) => data(api.post("/org/super-admins", body));
export const resetSuperAdminPassword = (id, currentPassword) =>
  data(api.post(`/org/super-admins/${id}/reset-password`, { current_password: currentPassword }));
export const setSuperAdminActive = (id, isActive, currentPassword) =>
  data(api.put(`/org/super-admins/${id}`, { is_active: isActive, current_password: currentPassword }));

// Recipe plans and action plans (backend: api/action_plans.py)
export const getRecipePlan = (productId) => data(api.get(`/products/${productId}/recipe-plan`));
export const saveRecipePlan = (productId, plan) => data(api.put(`/products/${productId}/recipe-plan`, plan));
export const saveProjectPlan = (categoryId, plan) => data(api.put(`/categories/${categoryId}/default-plan`, plan));
export const getActionPlans = (productId, round) => data(api.get(`/products/${productId}/action-plans`, { params: round ? { round } : {} }));
export const createActionPlan = (productId, plan) => data(api.post(`/products/${productId}/action-plans`, plan));
export const updateActionPlan = (planId, plan) => data(api.put(`/action-plans/${planId}`, plan));
export const deleteActionPlan = (planId) => data(api.delete(`/action-plans/${planId}`));
export const allocationHistory = (planId) => data(api.get(`/admin/action-plans/${planId}/allocation-history`));
export const allocatePlan = (planId, members) => data(api.put(`/admin/action-plans/${planId}/members`, { members }));
export const myActionPlans = () => data(api.get("/my/action-plans"));

// Admin report page: each action plan's team and the surveys each member took
export const getPlanSurveys = (productId, round) =>
  data(api.get(`/admin/products/${productId}/plan-surveys`, { params: round ? { round } : {} }));

// Projects (backend: api/projects.py): one product per project, linked to its catalog setup
export const listProjects = () => data(api.get("/org/projects"));
export const projectOptions = () => data(api.get("/org/projects/options"));
export const createProject = (body) => data(api.post("/org/projects", body));
export const getProject = (id) => data(api.get(`/org/projects/${id}`));
export const updateProject = (id, body) => data(api.put(`/org/projects/${id}`, body));
export const endProject = (id) => data(api.post(`/org/projects/${id}/end`));
export const deleteProject = (id) => data(api.delete(`/org/projects/${id}`));
// Admin: projects read-only, and their survey plans (teams are allocated with allocatePlan)
export const adminProjects = () => data(api.get("/admin/projects"));
export const adminProject = (id) => data(api.get(`/admin/projects/${id}`));
export const adminProjectPlans = (id) => data(api.get(`/admin/projects/${id}/plans`));
// Edit response target: the project's total consumer responses (Super Admin only)
export const setProjectTarget = (id, totalTarget) => data(api.put(`/org/projects/${id}/target`, { total_target: totalTarget }));
// The project's Recipe plan (its action plans) and starting its survey: surveys start only from a project
// Recording access (backend api/recordings.py): the product passkey (never returned), its Admins, the log
export const getRecordingAccess = (productId) => data(api.get(`/org/products/${productId}/recording-access`));
export const recordingAdmins = () => data(api.get("/org/recording-access/admins"));
export const setRecordingPasskey = (productId, passkey) => data(api.put(`/org/products/${productId}/recording-access/passkey`, { passkey }));
export const setRecordingAdmins = (productId, userIds) => data(api.put(`/org/products/${productId}/recording-access/admins`, { user_ids: userIds }));
export const shareRecordingPasskey = (productId, passkey, userIds) =>
  data(api.post(`/org/products/${productId}/recording-access/share`, { passkey, user_ids: userIds }));
// The organization's recording passkey: ONE for all products
export const getOrgPasskey = () => data(api.get("/org/recording-passkey"));
export const setOrgPasskey = (passkey) => data(api.put("/org/recording-passkey", { passkey }));
export const shareOrgPasskey = (passkey, userIds) => data(api.post("/org/recording-passkey/share", { passkey, user_ids: userIds }));
export const recordingAccessHistory = (productId) => data(api.get(`/org/products/${productId}/recording-access/history`));
export const getProductSummary = (productId, version) =>
  data(api.get("/org/projects/product-summary", { params: { product_id: productId, ...(version ? { version } : {}) } }));
export const getProjectPlans = (id) => data(api.get(`/org/projects/${id}/plans`));
export const addProjectPlan = (id, plan) => data(api.post(`/org/projects/${id}/plans`, plan));
export const startProjectSurvey = (id, body) => data(api.post(`/org/projects/${id}/start`, body));
// Pending setup (Dashboard) and the readiness of every product and project (api/setup.py)
export const getSetup = () => data(api.get("/org/setup"));