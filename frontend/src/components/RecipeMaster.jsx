import { useCallback, useEffect, useState } from "react";
import { errorText, getRecipeMaster } from "../services/nextApi";

/* Super Admin: Recipe Master (requirements points 1 and 3): every recipe with its
   cost per kg and standards result. Standards are set per product, on the product's
   Recipe standards tab (Products > the product > Recipe standards). */
const money = (v) => (v == null ? "—" : `₹${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);

export default function RecipeMaster({ onOpenProduct }) {
  const [state, setState] = useState({ loading: true, error: "", recipes: [], standards: [] });
  const load = useCallback(async () => {
    try {
      const m = await getRecipeMaster();
      setState({ loading: false, error: "", recipes: m.recipes || [] });
    } catch (err) {
      setState((x) => ({ ...x, loading: false, error: errorText(err, "Recipes couldn't be loaded.") }));
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  return (
    <section className="pc" aria-labelledby="recipe-master-title">
      <div className="home-head">
        <div className="page-heading">
          <h1 id="recipe-master-title">Recipe Master</h1>
          <p>Every product's current recipe, its cost per kg and whether it meets its recipe standards. Click a product to set its standards.</p>
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
    </section>
  );
}
