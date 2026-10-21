import axios from "axios";
import { API_BASE_URL } from "./feedbackApi";

// Product-wise feedback history and consolidated reports (backend: app/api/product_feedback.py).
const api = axios.create({ baseURL: API_BASE_URL });

// GET /product-feedback -> { products: [...], unlinked_count }
export async function getProductFeedbackList() {
  const res = await api.get("/product-feedback");
  return res.data;
}

// GET /product-feedback/{id}[?round=N] -> { product, summary, responses: [...], report, rounds, round }
export async function getProductFeedback(productId, round = null) {
  const res = await api.get(`/product-feedback/${productId}`, { params: round != null ? { round } : {} });
  return res.data;
}

// POST /product-feedback/{id}/consolidate[?round=N] -> the new report (takes 1-3 minutes)
export async function generateConsolidatedReport(productId, round = null) {
  const res = await api.post(`/product-feedback/${productId}/consolidate`, null, {
    params: round != null ? { round } : {},
    timeout: 15 * 60 * 1000,
  });
  return res.data;
}

// GET /product-feedback/unlinked -> { feedback: [...] } recordings not linked to a survey
export async function getUnlinkedFeedback() {
  const res = await api.get("/product-feedback/unlinked");
  return res.data;
}

// GET /product-feedback/surveys -> { surveys: [...], total } every survey, newest first
export async function getRecentSurveys(limit = 500) {
  const res = await api.get("/product-feedback/surveys", { params: { limit } });
  return res.data;
}

// DELETE /product-feedback/surveys/{id}: removes a survey completely
// (answers, analysis, recording); every count updates.
export async function deleteSurvey(responseId) {
  const res = await api.delete(`/product-feedback/surveys/${responseId}`);
  return res.data;
}