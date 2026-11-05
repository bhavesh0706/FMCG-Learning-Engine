import axios from "axios";
import { API_BASE_URL } from "./feedbackApi";

/*
  Next update (requirements PDF): Recipe Master, standards, attachments, master lists,
  survey form approval, report reviews, the Super Admin's surveys, report downloads.
  axios.create() clients get the sign-in token automatically (services/auth.js).
*/
const api = axios.create({ baseURL: API_BASE_URL });
const data = (p) => p.then((r) => r.data);

export const errorText = (err, fallback) => err?.response?.data?.detail || err?.message || fallback;

// Recipe Master, standards, attachments
export const getRecipeMaster = () => data(api.get("/recipes"));
export const listStandards = () => data(api.get("/recipe-standards"));
export const addStandard = (body) => data(api.post("/recipe-standards", body));
export const changeStandard = (id, body) => data(api.put(`/recipe-standards/${id}`, body));
export const removeStandard = (id) => data(api.delete(`/recipe-standards/${id}`));
export const uploadAttachment = (productId, file) => {
  const form = new FormData();
  form.append("file", file);
  return data(api.post(`/products/${productId}/recipe/attachments`, form));
};
export const removeAttachment = (id) => data(api.delete(`/recipe-attachments/${id}`));

/** Download a protected file (attachment, Excel, PowerPoint) and save it with its name. */
export async function downloadFile(path, fallbackName) {
  const res = await api.get(path, { responseType: "blob" });
  const header = res.headers?.["content-disposition"] || "";
  const name = decodeURIComponent((header.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i) || [])[1] || fallbackName);
  const url = URL.createObjectURL(res.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
export const downloadAttachment = (a) => downloadFile(`/recipe-attachments/${a.id}/file`, a.filename);
export const downloadReport = (productId, kind, round) =>
  downloadFile(`/product-feedback/${productId}/export.${kind}${round ? `?round=${round}` : ""}`, `report.${kind}`);

// Master lists
export const getMasters = (all = false) => data(api.get("/masters", { params: all ? { all: true } : {} }));
export const addMaster = (kind, name) => data(api.post(`/admin/masters/${kind}`, { name }));
export const changeMaster = (kind, id, body) => data(api.patch(`/admin/masters/${kind}/${id}`, body));

// Survey form approval (admins)
export const listSurveyForms = (status = "pending") => data(api.get("/admin/survey-forms", { params: { status } }));
export const approveSurveyForm = (productId, note) => data(api.post(`/admin/products/${productId}/survey-form/approve`, { note }));
export const returnSurveyForm = (productId, note) => data(api.post(`/admin/products/${productId}/survey-form/return`, { note }));

// Report review chain
export const submitReport = (responseId, note) => data(api.post(`/surveys/responses/${responseId}/report/submit`, { note }));
export const reportHistory = (responseId) => data(api.get(`/surveys/responses/${responseId}/report/history`));
export const listReportReviews = (status = "pending_admin") => data(api.get("/admin/report-reviews", { params: { status } }));
export const approveReport = (responseId, note) => data(api.post(`/admin/report-reviews/${responseId}/approve`, { note }));
export const returnReport = (responseId, note) => data(api.post(`/admin/report-reviews/${responseId}/return`, { note }));

// Super Admin: every survey
export const listOrgSurveys = (params = {}) => data(api.get("/org/surveys", { params }));
export const getOrgSurvey = (id) => data(api.get(`/org/surveys/${id}`));
export const reviewOrgSurvey = (id, note) => data(api.post(`/org/surveys/${id}/review`, { note }));

export const REPORT_LABELS = {
  generated: "Generated",
  pending_admin: "Pending Admin review",
  returned: "Returned to survey person",
  forwarded: "Approved by Admin · forwarded",
  reviewed: "Reviewed by Super Admin",
};
export const REPORT_TONES = { generated: "neutral", pending_admin: "warn", returned: "neg", forwarded: "act", reviewed: "ok" };

// Custom reports from a prompt (one product or many): options, plan, file
export const getReportOptions = () => data(api.get("/reports/options"));
export const planReport = (body) => data(api.post("/reports/plan", body));
export const downloadPlannedReport = (requestId, format) => downloadFile(`/reports/file/${requestId}`, `report.${format}`);
