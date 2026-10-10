import axios from "axios";
import { API_BASE_URL } from "./feedbackApi";

// Analysis queue (backend: app/api/analysis.py). Uploads return at once;
// the recording is analyzed in the background while the app stays usable.
const api = axios.create({ baseURL: API_BASE_URL });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// POST /analysis/jobs -> { job_id, status, position }
export async function startAnalysis(file, responseId, onProgress) {
  const form = new FormData();
  form.append("file", file);
  if (responseId != null) form.append("response_id", String(responseId));
  const res = await api.post("/analysis/jobs", form, {
    onUploadProgress: (e) => {
      if (e.total && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    },
  });
  return res.data;
}

// GET /analysis/jobs/{id}
export async function getAnalysisJob(jobId) {
  const res = await api.get(`/analysis/jobs/${jobId}`);
  return res.data;
}

// GET /analysis/jobs -> { jobs: [...] } still queued or processing
export async function getActiveJobs() {
  const res = await api.get("/analysis/jobs");
  return res.data;
}

/**
 * Waits for a job to finish, reporting each status. Survives short network
 * drops (keeps polling). Resolves with the finished job; rejects if it failed.
 */
export async function waitForAnalysis(jobId, onStatus, { intervalMs = 3000, signal } = {}) {
  let misses = 0;
  for (;;) {
    if (signal?.aborted) throw Object.assign(new Error("cancelled"), { cancelled: true });
    try {
      const job = await getAnalysisJob(jobId);
      misses = 0;
      onStatus?.(job);
      if (job.status === "done") return job;
      if (job.status === "failed") {
        throw Object.assign(new Error(job.error || "The analysis failed."), { analysisFailed: true });
      }
    } catch (err) {
      if (err?.analysisFailed || err?.response?.status === 404) throw err;
      misses += 1; // network hiccup: keep trying for a while
      if (misses > 40) throw err;
    }
    await sleep(intervalMs);
  }
}

// Lets every list on the page know that data changed (e.g. an analysis finished).
import { DATA_CHANGED } from "./appEvents";
export { DATA_CHANGED };
export function announceDataChanged() {
  window.dispatchEvent(new Event(DATA_CHANGED));
}