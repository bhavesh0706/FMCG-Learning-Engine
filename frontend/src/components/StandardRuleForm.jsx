import { useEffect, useState } from "react";
import { addStandard, changeStandard, errorText } from "../services/nextApi";
import { getCategories } from "../services/feedbackApi";
import Select from "./Select";
import { unitCode, unitGroups } from "../services/units";

/*
  One recipe standard rule: add or edit (services/recipe_tools.py checks it).
  Scope, exactly one:
    productId given  -> this product only (product page)
    otherwise        -> chosen explicitly: all products, or one category (Recipe Master)
  There is no default "everything": a rule applies only where it was meant to.
*/
export const KINDS = {
  required_ingredient: "Must contain an ingredient",
  ingredient_share: "Ingredient Usage % between",
  ingredient_cost_per_kg: "Ingredient cost at most (per UOM)",
  max_cost_per_kg: "Recipe cost at most (per UOM, whole recipe)",
  costs_complete: "Every ingredient has a Cost per KG",
  custom: "Custom rule (by quantity)",
};
const NEEDS_INGREDIENT = ["required_ingredient", "ingredient_share", "ingredient_cost_per_kg", "custom"];
// Rules whose limits have a unit of measure (quantity, or ₹ per unit).
const HAS_UOM = ["custom", "ingredient_cost_per_kg", "max_cost_per_kg"];
const asText = (v) => (v == null ? "" : String(v));

export function ruleCondition(r) {
  const money = (v) => `₹${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  const u = r.unit || "kg";
  if (r.kind === "required_ingredient") return `contains ${r.ingredient}`;
  if (r.kind === "ingredient_share") {
    const lo = r.min_value, hi = r.max_value;
    return `${r.ingredient}: ${lo != null && hi != null ? `${lo}% – ${hi}%` : lo != null ? `at least ${lo}%` : `at most ${hi}%`}`;
  }
  if (r.kind === "ingredient_cost_per_kg") return `${r.ingredient} at most ${money(r.max_value)}/${u}`;
  if (r.kind === "max_cost_per_kg") return `recipe at most ${money(r.max_value)}/${u}`;
  if (r.kind === "custom") {
    if (!r.ingredient) return "written rule, checked manually";
    const lo = r.min_value, hi = r.max_value;
    return `${r.ingredient}: ${lo != null && hi != null ? `${lo} – ${hi} ${u}` : lo != null ? `at least ${lo} ${u}` : `at most ${hi} ${u}`}`;
  }
  return "every ingredient costed";
}

export default function StandardRuleForm({ productId = null, rule = null, onSaved, onCancel, initialKind = null }) {
  const [f, setF] = useState(() => ({
    name: rule?.name || "", kind: rule?.kind || initialKind || "ingredient_share", ingredient: rule?.ingredient || "",
    min_value: asText(rule?.min_value), max_value: asText(rule?.max_value), unit: unitCode(rule?.unit) || "kg",
    scope: rule ? (rule.applies_to_all ? "all" : rule.category_id ? String(rule.category_id) : "") : "",
  }));
  const [categories, setCategories] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (productId) return;
    getCategories().then((r) => setCategories(Array.isArray(r) ? r : r?.categories || [])).catch(() => {});
  }, [productId]);
  const num = (v) => (String(v).trim() === "" ? null : Number(String(v).replace(/,/g, "")));
  const save = async () => {
    setError("");
    if (!productId && !f.scope) return setError("Choose what the rule applies to.");
    const body = {
      name: f.name.trim(), kind: f.kind, ingredient: NEEDS_INGREDIENT.includes(f.kind) ? f.ingredient.trim() || null : null,
      min_value: ["ingredient_share", "custom"].includes(f.kind) ? num(f.min_value) : null,
      max_value: ["ingredient_share", "ingredient_cost_per_kg", "max_cost_per_kg", "custom"].includes(f.kind) ? num(f.max_value) : null,
      unit: HAS_UOM.includes(f.kind) ? f.unit : null,
      product_id: productId || null,
      category_id: !productId && f.scope && f.scope !== "all" ? Number(f.scope) : null,
      applies_to_all: !productId && f.scope === "all",
    };
    setBusy(true);
    try {
      const saved = rule ? await changeStandard(rule.id, body) : await addStandard(body);
      if (!rule) setF((x) => ({ ...x, name: "", ingredient: "", min_value: "", max_value: "" }));
      onSaved?.(saved);
    } catch (err) {
      setError(errorText(err, "The rule couldn't be saved."));
    } finally {
      setBusy(false);
    }
  };
  const isCost = f.kind === "ingredient_cost_per_kg" || f.kind === "max_cost_per_kg";
  const units = unitGroups();
  const uomSelect = (
    <Select className="input std-uom" value={f.unit} aria-label="UOM (unit of measure)" title="UOM (unit of measure)"
      onChange={(e) => setF({ ...f, unit: e.target.value })}>
      {units.flatMap(([cat, us]) => us.map(([code, label]) => (
        <option key={`${cat}-${code}`} value={code} title={cat}>{isCost ? `per ${code}` : code} · {label}</option>
      )))}
    </Select>
  );
  return (
    <div className="std-form">
      <Select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} aria-label="Rule type">
        {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </Select>
      <input className="input" placeholder={f.kind === "custom" ? "Rule name, e.g. Paneer quantity" : "Rule name, e.g. Cardamom at most ₹2,500/kg"} value={f.name} aria-label="Rule name"
        onChange={(e) => setF({ ...f, name: e.target.value })} />
      {NEEDS_INGREDIENT.includes(f.kind) && (
        <input className="input" placeholder="Ingredient, e.g. cardamom powder" value={f.ingredient} aria-label="Ingredient"
          onChange={(e) => setF({ ...f, ingredient: e.target.value })} />
      )}
      {f.kind === "ingredient_share" && (
        <input className="input" inputMode="decimal" placeholder="Min %" value={f.min_value} aria-label="Min %"
          onChange={(e) => setF({ ...f, min_value: e.target.value })} />
      )}
      {f.kind === "custom" && (
        <>
          <input className="input" inputMode="decimal" placeholder={`Min ${f.unit}`} value={f.min_value} aria-label={`Min quantity in ${f.unit}`}
            onChange={(e) => setF({ ...f, min_value: e.target.value })} />
          <input className="input" inputMode="decimal" placeholder={`Max ${f.unit}`} value={f.max_value} aria-label={`Max quantity in ${f.unit}`}
            onChange={(e) => setF({ ...f, max_value: e.target.value })} />
          {uomSelect}
        </>
      )}
      {(f.kind === "ingredient_share" || isCost) && (
        <input className="input" inputMode="decimal" placeholder={isCost ? `Max ₹ per ${f.unit}` : "Max %"} value={f.max_value}
          aria-label={isCost ? `Max ₹ per ${f.unit}` : "Max %"} onChange={(e) => setF({ ...f, max_value: e.target.value })} />
      )}
      {isCost && uomSelect}
      {!productId && (
        <Select className="input" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })} aria-label="Applies to">
          <option value="">Applies to…</option>
          <option value="all">All products</option>
          {categories.map((c) => <option key={c.id} value={c.id}>Only {c.name}</option>)}
        </Select>
      )}
      <button type="button" className="btn btn-primary btn-sm" disabled={busy || f.name.trim().length < 3} onClick={save}>
        {busy ? "Saving…" : rule ? "Save rule" : productId ? "Add product rule" : "Add rule"}
      </button>
      {onCancel && <button type="button" className="btn btn-secondary btn-sm" onClick={onCancel}>Cancel</button>}
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}