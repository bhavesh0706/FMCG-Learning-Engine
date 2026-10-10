// App-wide change signals. Screens listen; the API layer announces.
// Same event name as analysisApi's DATA_CHANGED, so existing auto-refresh lists react too.
export const DATA_CHANGED = "fmcg:data-changed";
export const CATALOG_CHANGED = "fmcg:catalog-changed";

export function announceCatalogChanged() {
  window.dispatchEvent(new Event(CATALOG_CHANGED));
  window.dispatchEvent(new Event(DATA_CHANGED));
}