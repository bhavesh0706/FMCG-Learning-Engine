import axios from "axios";
import { useEffect, useState } from "react";
import { API_BASE_URL } from "./feedbackApi";

/*
  LOV (list of values) from the database: m_othermastername + m_othermasterdata
  (backend api/lov.py). Admins change them in Catalog > Master lists.

  Each screen keeps its old list in the code as the FALLBACK (used until the lists
  load, or if they can't be loaded) and registers how to update it with onLovLoaded().
  The App root calls useLov(), so the whole page re-renders once the lists arrive.
*/
const api = axios.create({ baseURL: API_BASE_URL });
const data = (p) => p.then((r) => r.data);

export const getLov = (all = false) => data(api.get("/lov", { params: all ? { all: true } : {} }));
export const addLovValue = (masterCode, label, code, parentId) =>
  data(api.post(`/admin/lov/${masterCode}`, { label, code, parent_id: parentId }));
export const changeLovValue = (id, body) => data(api.patch(`/admin/lov/values/${id}`, body));

let lists = null; // { USAGE_PERIOD: { name, is_system, values: [{ id, code, label, sort_order, extra, is_active }] }, ... }
let loading = null;
const appliers = new Set();
const listeners = new Set();

/** Active values of one list, in order; null when the list isn't loaded / is empty. */
export function lovValues(masterCode) {
  const values = lists?.[masterCode]?.values;
  return values && values.length ? values : null;
}

/** Register a function that copies the loaded lists into a screen's constants. */
export function onLovLoaded(apply) {
  appliers.add(apply);
  if (lists) apply();
}

function publish(next) {
  lists = next;
  appliers.forEach((apply) => {
    try {
      apply();
    } catch {
      /* a bad value must not break the page: that screen keeps its fallback */
    }
  });
  listeners.forEach((fn) => fn());
}

/** Load (or reload after an admin change) the lists. Never throws. */
export function loadLov(force = false) {
  if (loading && !force) return loading;
  loading = getLov()
    .then(publish)
    .catch(() => {
      loading = null; // try again on the next call
    });
  return loading;
}

/** Re-renders the caller when the lists (re)load. Use once, in the App root. */
export function useLov(enabled = true) {
  const [, setVersion] = useState(0);
  useEffect(() => {
    if (!enabled) return undefined;
    const fn = () => setVersion((v) => v + 1);
    listeners.add(fn);
    loadLov();
    return () => listeners.delete(fn);
  }, [enabled]);
}

/* ---------- Helpers to copy a list into an existing constant (in place) ---------- */

/** { code: label } object: replace its contents with the list (open lists: added / hidden values). */
export function fillMap(target, masterCode, toValue = (v) => v.label) {
  const values = lovValues(masterCode);
  if (!values) return;
  Object.keys(target).forEach((k) => delete target[k]);
  values.forEach((v) => { target[v.code] = toValue(v); });
}

/** { code: anything } object: update only the codes it already has (system lists). */
export function patchMap(target, masterCode, update) {
  (lovValues(masterCode) || []).forEach((v) => {
    if (Object.prototype.hasOwnProperty.call(target, v.code)) {
      const next = update(target[v.code], v);
      if (next !== undefined) target[v.code] = next;
    }
  });
}

/** Array constant: replace its items with the list. */
export function fillArray(target, masterCode, toItem) {
  const values = lovValues(masterCode);
  if (!values) return;
  target.splice(0, target.length, ...values.map(toItem));
}

/** Values of a list inside another list (STATE of a country, CITY of a state). */
export function lovChildren(masterCode, parentId) {
  if (parentId == null) return [];
  return (lovValues(masterCode) || []).filter((v) => v.parent_id === parentId);
}

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/** The value whose label (or one of its aliases, e.g. "Bangalore") matches a place name; else null. */
export function lovMatch(values, name) {
  const want = norm(name);
  if (!want) return null;
  const names = (v) => [v.label, ...(v.extra?.aliases || [])].map(norm);
  return values.find((v) => names(v).includes(want))
    // "Pune City", "Greater Mumbai", "Bengaluru Urban" ...
    || values.find((v) => names(v).some((n) => n && (want.startsWith(`${n} `) || want.endsWith(` ${n}`))))
    || null;
}

/** Label of one code, or the fallback. */
export function lovLabel(masterCode, code, fallback) {
  return lovValues(masterCode)?.find((v) => v.code === code)?.label ?? fallback;
}
