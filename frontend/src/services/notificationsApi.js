import axios from "axios";
import { API_BASE_URL } from "../config";

// Notification bell (backend: app/api/notifications.py).
// The sign-in token is added to every request by services/auth.js.
const api = axios.create({ baseURL: API_BASE_URL });

// GET /notifications/unread-count -> { unread_count, latest_id }  (polled every 30 s)
export async function getUnreadCount() {
  const res = await api.get("/notifications/unread-count");
  return res.data;
}

// GET /notifications -> { unread_count, latest_id, notifications: [...] }
export async function getNotifications({ limit = 30, unreadOnly = false } = {}) {
  const res = await api.get("/notifications", { params: { limit, unread_only: unreadOnly } });
  return res.data;
}

// POST /notifications/{id}/read -> { notification, unread_count }
export async function markNotificationRead(id) {
  const res = await api.post(`/notifications/${id}/read`);
  return res.data;
}

// POST /notifications/read-all -> { updated, unread_count }
export async function markAllNotificationsRead() {
  const res = await api.post("/notifications/read-all");
  return res.data;
}
