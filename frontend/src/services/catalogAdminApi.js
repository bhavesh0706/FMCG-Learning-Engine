import axios from "axios";
import { API_BASE_URL } from "./feedbackApi";
import { announceCatalogChanged } from "./appEvents";

// Catalog management for admins (backend: app/api/catalog.py and app/api/recipes.py).
const api = axios.create({ baseURL: API_BASE_URL });
const data = (p) => p.then((r) => r.data);

// Every successful change tells the whole app (survey lists, Home, reports),
// so no screen keeps an old copy of the catalog. Add new write calls with `change`.
const change = (p) =>
  p.then((r) => {
    announceCatalogChanged();
    return r.data;
  });

// Readable message from any API error.
export function catalogError(err, fallback = "Something went wrong. Try again.") {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg).replace("Value error, ", "");
  if (err?.code === "ERR_NETWORK") return `Can't reach the backend at ${API_BASE_URL}.`;
  return fallback;
}

// Categories
export const listCategories = (includeInactive = true) =>
  data(api.get("/categories", { params: { include_inactive: includeInactive } }));
export const createCategory = (body) => change(api.post("/categories", body));
export const updateCategory = (id, body) => change(api.put(`/categories/${id}`, body));

// Products
export const listProducts = (categoryId, includeInactive = true) =>
  data(api.get("/products", { params: { category_id: categoryId, include_inactive: includeInactive } }));
export const createProduct = (body) => change(api.post("/products", body));
export const updateProduct = (id, body) => change(api.put(`/products/${id}`, body));

// Survey questions
export const listQuestions = (productId) => data(api.get(`/products/${productId}/questions`));
export const createQuestion = (productId, body) => change(api.post(`/products/${productId}/questions`, body));
export const updateQuestion = (id, body) => change(api.put(`/questions/${id}`, body));
export const removeQuestion = (id) => change(api.delete(`/questions/${id}`));
export const reorderQuestions = (productId, ids) =>
  change(api.put(`/products/${productId}/questions/order`, { question_ids: ids }));

// Recipe
export const getRecipe = (productId) => data(api.get(`/products/${productId}/recipe`));
export const saveRecipe = (productId, recipe) => change(api.put(`/products/${productId}/recipe`, recipe));

// Who may survey a product ([] = every executive)
export const getProductExecutives = (productId) => data(api.get(`/admin/products/${productId}/executives`));
export const saveProductExecutives = (productId, userIds) =>
  change(api.put(`/admin/products/${productId}/executives`, { user_ids: userIds }));
export const listTeam = () => data(api.get("/admin/users"));

// Question templates: reusable starting questions for new products (stored in MySQL)
export const listQuestionTemplates = (categoryId) =>
  data(api.get("/question-templates", { params: categoryId ? { category_id: categoryId } : {} }));
export const createQuestionTemplate = (body) => change(api.post("/question-templates", body));
export const updateQuestionTemplate = (id, body) => change(api.put(`/question-templates/${id}`, body));
export const removeQuestionTemplate = (id) => change(api.delete(`/question-templates/${id}`));

// Permanent deletion (only for deactivated items with no surveys / no products)
export const deleteProductPermanently = (id) => change(api.delete(`/products/${id}/permanent`));
export const deleteCategoryPermanently = (id) => change(api.delete(`/categories/${id}/permanent`));