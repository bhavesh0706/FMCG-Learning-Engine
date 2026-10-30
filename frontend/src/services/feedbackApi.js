import axios from "axios";
import { API_BASE_URL } from "../config";

// All calls to the FastAPI backend live here, so App.jsx only deals with the UI.
// The backend address comes from VITE_API_BASE_URL (see src/config.js).
// Re-exported so existing imports from this file keep working.
export { API_BASE_URL };

const api = axios.create({
  baseURL: API_BASE_URL,
});

// ============================================================
// VIDEO / AUDIO FEEDBACK
// ============================================================

// POST /video/upload
// Upload media and optionally attach it to a SurveyResponse.
// onProgress receives the upload percentage (0–100).
export async function uploadVideo(
  file,
  onProgress,
  responseId = null
) {
  const formData = new FormData();

  formData.append("file", file);

  if (responseId !== null && responseId !== undefined) {
    formData.append(
      "response_id",
      String(responseId)
    );
  }

  const res = await api.post(
    "/video/upload",
    formData,
    {
      onUploadProgress: (e) => {
        if (
          e.total &&
          onProgress
        ) {
          onProgress(
            Math.round(
              (e.loaded / e.total) * 100
            )
          );
        }
      },
    }
  );

  return res.data;
}


// GET /feedback -> list of saved analyses
export async function getFeedbackHistory() {
  const res = await api.get("/feedback");

  return res.data?.feedback || [];
}


// GET /feedback/{id} -> one saved analysis
// with summary, chapters and transcript
export async function getFeedbackById(id) {
  const res = await api.get(
    `/feedback/${id}`
  );

  return res.data;
}


// URL for the <video> tag and download link.
// No request is made here.
export function getFeedbackVideoUrl(id) {
  return `${API_BASE_URL}/feedback/${id}/video`;
}


// DELETE /feedback/{id}
export async function deleteFeedback(id) {
  const res = await api.delete(
    `/feedback/${id}`
  );

  return res.data;
}


// ============================================================
// CATALOG
// ============================================================

// GET /categories
export async function getCategories() {
  const res = await api.get(
    "/categories"
  );

  return res.data || [];
}


// GET /products
// GET /geo/reverse: the phone/computer position -> a readable place for "Survey location".
export async function reverseGeocode(latitude, longitude) {
  const res = await api.get("/geo/reverse", { params: { lat: latitude, lng: longitude } });
  return res.data; // { label, locality, city, state, country, latitude, longitude }
}

export async function getProducts() {
  const res = await api.get(
    "/products"
  );

  return res.data || [];
}


// GET /products/{productId}
export async function getProduct(
  productId
) {
  const res = await api.get(
    `/products/${productId}`
  );

  return res.data;
}


// GET /products/{productId}/questions
export async function getProductQuestions(
  productId
) {
  const res = await api.get(
    `/products/${productId}/questions`
  );

  return res.data || [];
}


// ============================================================
// SURVEY RESPONDENT
// ============================================================

// POST /surveys/respondents
export async function createRespondent(
  respondentData
) {
  const res = await api.post(
    "/surveys/respondents",
    respondentData
  );

  return res.data;
}


// GET /surveys/respondents/{respondentId}
export async function getRespondent(
  respondentId
) {
  const res = await api.get(
    `/surveys/respondents/${respondentId}`
  );

  return res.data;
}


// ============================================================
// SURVEY RESPONSE
// ============================================================

// POST /surveys/responses
export async function createSurveyResponse(
  responseData
) {
  const res = await api.post(
    "/surveys/responses",
    responseData
  );

  return res.data;
}


// GET /surveys/responses/{responseId}
export async function getSurveyResponse(
  responseId
) {
  const res = await api.get(
    `/surveys/responses/${responseId}`
  );

  return res.data;
}


// PUT /surveys/responses/{responseId}/answers
export async function saveSurveyAnswers(
  responseId,
  answers
) {
  const res = await api.put(
    `/surveys/responses/${responseId}/answers`,
    {
      answers,
    }
  );

  return res.data;
}


// POST /surveys/responses/{responseId}/submit
export async function submitSurveyResponse(
  responseId
) {
  const res = await api.post(
    `/surveys/responses/${responseId}/submit`
  );

  return res.data;
}