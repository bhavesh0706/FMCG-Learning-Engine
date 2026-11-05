import { useCallback, useEffect, useState } from "react";
import { addStandard, errorText, getRecipeMaster, listStandards, removeStandard } from "../services/nextApi";

/* Super Admin: Recipe Master (requirements points 1 and 3): every recipe with its
   cost per kg and standards result, and the standard rules recipes are checked against. */
const KINDS = {
  required_ingredient: "Must contain an ingredient",
  ingredient_share: "Ingredient Usage % between",
  max_cost_per_kg: "Recipe cost per kg at most",
  costs_complete: "Every ingredient has a Cost per KG",
};
const money = (v) => (v == null ? "—" : `₹${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);

function RuleForm({ onAdded }) {
  const [f, setF] = useState({ name: "", kind: "ingredient_share", ingredient: "", min_value: "", max_value: "" });
  const [error, setError] = useState("");
  const num = (v) => (v === "" ? null : Number(v));
  const add = async () => {
    setError("");
    try {
      await addStandard({ name: f.name.trim(), kind: f.kind, ingredient: f.ingredient.trim() || null, min_value: num(f.min_value), max_value: num(f.max_value) });
      setF({ name: "", kind: f.kind, ingredient: "", min_value: "", max_value: "" });
      onAdded();
    } catch (err) {
      setError(errorText(err, "The rule couldn't be added."));
    }
  };
  const needsIngredient = f.kind === "required_ingredient" || f.kind === "ingredient_share";
  return (
    <div className="std-form">
      <input className="input" placeholder="Rule name, e.g. Sugar at most 25%" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} aria-label="Rule type">
        {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      {needsIngredient && <input className="input" placeholder="Ingredient, e.g. sugar" value={f.ingredient} onChange={(e) => setF({ ...f, ingredient: e.target.value })} />}
      {f.kind === "ingredient_share" && <input className="input" inputMode="decimal" placeholder="Min %" value={f.min_value} onChange={(e) => setF({ ...f, min_value: e.target.value })} />}
      {(f.kind === "ingredient_share" || f.kind === "max_cost_per_kg") && (
        <input className="input" inputMode="decimal" placeholder={f.kind === "max_cost_per_kg" ? "Max ₹ per kg" : "Max %"} value={f.max_value} onChange={(e) => setF({ ...f, max_value: e.target.value })} />
      )}
      <button type="button" className="btn btn-primary btn-sm" disabled={f.name.trim().length < 3} onClick={add}>Add rule</button>
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}

export default function RecipeMaster({ onOpenProduct }) {
  const [state, setState] = useState({ loading: true, error: "", recipes: [], standards: [] });
  const load = useCallback(async () => {
    try {
      const [m, s] = await Promise.all([getRecipeMaster(), listStandards()]);
      setState({ loading: false, error: "", recipes: m.recipes || [], standards: s.standards || [] });
    } catch (err) {
      setState((x) => ({ ...x, loading: false, error: errorText(err, "Recipes couldn't be loaded.") }));
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  const remove = async (r) => {
    if (!window.confirm(`Remove the rule “${r.name}”?`)) return;
    await removeStandard(r.id).catch(() => {});
    load();
  };
  return (
    <section className="pc" aria-labelledby="recipe-master-title">
      <div className="home-head">
        <div className="page-heading">
          <h1 id="recipe-master-title">Recipe Master</h1>
          <p>Every product's current recipe, its cost per kg and whether it meets the recipe standards.</p>
        </div>
      </div>
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      <div className="panel">
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>Product</th><th>Category</th><th>Version</th><th className="num-cell">Ingredients</th><th className="num-cell">Cost per kg</th><th>Standards</th><th className="num-cell">Files</th></tr>
            </thead>
            <tbody>
              {state.loading ? <tr><td colSpan={7} className="muted">Loading…</td></tr> : state.recipes.map((r) => (
                <tr key={r.product_id} className={onOpenProduct ? "is-clickable" : ""} onClick={() => onOpenProduct?.(r.product_id)}>
                  <td><strong className="row-link">{r.product_name}</strong>{r.sku && <span className="cell-sub">{r.sku}</span>}</td>
                  <td>{r.category_name}</td>
                  <td>{r.has_recipe ? `v${r.version || 1}` : <span className="muted">No recipe</span>}</td>
                  <td className="num-cell">{r.ingredients}</td>
                  <td className="num-cell">{money(r.cost_per_kg)}{r.missing_costs ? <span className="cell-sub">{r.missing_costs} without cost</span> : null}</td>
                  <td>{!r.standards ? "—" : r.standards.checked === 0 ? <span className="muted">No rules</span> : (
                    <span className={`status-chip ${r.standards.passed ? "is-ok" : "is-neg"}`}>{r.standards.passed ? "All met" : `${r.standards.failed_count} not met`}</span>)}</td>
                  <td className="num-cell">{r.attachments}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="panel">
        <h2 className="report-h">Recipe standards</h2>
        <p className="muted">Rules every recipe is checked against; the result shows on each product's Recipe tab.</p>
        <ul className="std-results">
          {state.standards.map((r) => (
            <li key={r.id}>
              <span><strong>{r.name}</strong><span className="muted"> · {KINDS[r.kind]}{r.ingredient ? ` · ${r.ingredient}` : ""}
                {r.min_value != null ? ` · min ${r.min_value}` : ""}{r.max_value != null ? ` · max ${r.max_value}` : ""}{r.category_name ? ` · ${r.category_name} only` : ""}</span></span>
              <button type="button" className="link-btn danger-link" onClick={() => remove(r)}>Remove</button>
            </li>
          ))}
          {!state.standards.length && <li className="muted">No rules yet.</li>}
        </ul>
        <RuleForm onAdded={load} />
      </div>
    </section>
  );
}
