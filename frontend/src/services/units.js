import { lovValues } from "./lov";

/*
  Units of measure (UOM): from Other Master > Measurement unit only (GET /lov), grouped by
  Measurement category (Weight/Mass, Volume). Each unit's conversion factor is in extra.to_base
  (in its category's base: kg or L; 1 L counted as 1 kg for batch weights). No unit or factor is
  written in code: a unit added in Other Master is listed and calculated everywhere.
  SPELLINGS: other ways people type a master unit ("kgs", "ltr", "ton") -> that unit's code.
*/
const SPELLINGS = {
  kgs: "kg", kilo: "kg", kilos: "kg", kilogram: "kg", kilograms: "kg", gm: "g", gms: "g", gram: "g", grams: "g",
  milligram: "mg", milligrams: "mg", ug: "µg", "μg": "µg", microgram: "µg", micrograms: "µg",
  t: "MT", ton: "MT", tons: "MT", tonne: "MT", tonnes: "MT",
  ltr: "L", ltrs: "L", litre: "L", litres: "L", liter: "L", liters: "L", millilitre: "mL", milliliter: "mL",
  ul: "µL", "μl": "µL", kilolitre: "kL", kiloliter: "kL",
};

function masterUnits() {
  const cats = lovValues("UNIT_CATEGORY") || [];
  const units = lovValues("MEASUREMENT_UNIT") || [];
  return { cats, units };
}

/** [[category, [[code, name], ...]], ...] for the unit dropdowns. */
export function unitGroups() {
  const { cats, units } = masterUnits();
  return cats.map((c) => [c.label, units.filter((u) => u.parent_id === c.id).map((u) => [u.code, u.label])]).filter(([, us]) => us.length);
}

/** { "kg": { toKg, base, code }, "kgs": {...}, ... } (lower-case keys) from the master. */
export function unitTable() {
  const { units } = masterUnits();
  const table = {};
  for (const u of units) {
    const f = Number(u.extra?.to_base);
    if (f > 0) table[u.code.toLowerCase()] = { toKg: f, base: u.extra?.base || "kg", code: u.code };
  }
  for (const [spelling, code] of Object.entries(SPELLINGS)) {
    if (table[code.toLowerCase()] && !table[spelling]) table[spelling] = table[code.toLowerCase()];
  }
  return table;
}

/** kg per one `unit` (a master unit or its spelling), or undefined when it isn't a known unit. */
export const toKg = (unit, table = unitTable()) => table[String(unit || "").trim().toLowerCase()]?.toKg;

// The master's code for a unit typed before (e.g. "ml" -> "mL", "ltr" -> "L"); other text is kept.
export function unitCode(unit, groups = unitGroups()) {
  const u = String(unit || "").trim();
  if (!u) return "";
  const hit = unitTable()[u.toLowerCase()];
  if (hit && groups.some(([, us]) => us.some(([code]) => code === hit.code))) return hit.code;
  return u;
}
