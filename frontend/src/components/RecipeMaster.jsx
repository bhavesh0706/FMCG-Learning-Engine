import { useCallback, useEffect, useMemo, useState } from "react";
import KpiIcon from "./KpiIcon";
import { errorText, getRecipeMaster } from "../services/nextApi";

/* Super Admin: Recipe Master (requirements points 1 and 3): every recipe with its
   cost per kg and standards result, as one card per product. Standards are set per
   product, on the product's Recipe standards tab (Products > the product > Recipe standards). */
const money = (v) => (v == null ? "—" : `₹${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);

const FILTERS = [
  ["all", "All recipes", () => true],
  ["costs", "Missing costs", (r) => r.has_recipe && r.missing_costs > 0],
  ["failing", "Standards not met", (r) => r.standards && r.standards.checked > 0 && !r.standards.passed],
  ["norules", "No standards", (r) => !r.standards || r.standards.checked === 0],
];

// A stable accent per category, so products of one category share a colour.
const ACCENTS = ["brand", "teal", "act", "pos", "plum"];
const accentOf = (name) => ACCENTS[[...(name || "")].reduce((a, c) => a + c.charCodeAt(0), 0) % ACCENTS.length];

function standardsChip(r) {
  if (!r.has_recipe) return <span className="status-chip is-neutral">No recipe yet</span>;
  if (!r.standards || r.standards.checked === 0) return <span className="status-chip is-neutral">No standards set</span>;
  return r.standards.passed
    ? <span className="status-chip is-ok">✓ All {r.standards.checked} standards met</span>
    : <span className="status-chip is-neg">{r.standards.failed_count} of {r.standards.checked} not met</span>;
}

export default function RecipeMaster({ onOpenProduct }) {
  const [state, setState] = useState({ loading: true, error: "", recipes: [] });
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const load = useCallback(async () => {
    try {
      const m = await getRecipeMaster();
      setState({ loading: false, error: "", recipes: m.recipes || [] });
    } catch (err) {
      setState((x) => ({ ...x, loading: false, error: errorText(err, "Recipes couldn't be loaded.") }));
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const all = state.recipes;
  const kpis = useMemo(() => {
    const withRecipe = all.filter((r) => r.has_recipe);
    const ruled = withRecipe.filter((r) => r.standards && r.standards.checked > 0);
    return {
      recipes: withRecipe.length,
      ingredients: withRecipe.reduce((a, r) => a + (r.ingredients || 0), 0),
      costed: withRecipe.filter((r) => r.costing_complete).length,
      ruled: ruled.length,
      met: ruled.filter((r) => r.standards.passed).length,
    };
  }, [all]);
  const needle = q.trim().toLowerCase();
  const test = FILTERS.find(([k]) => k === filter)[2];
  const rows = all.filter((r) => test(r) && (!needle || [r.product_name, r.sku, r.category_name].join(" ").toLowerCase().includes(needle)));

  return (
    <section className="pc" aria-labelledby="recipe-master-title">
      <div className="home-head">
        <div className="page-heading">
          <h1 id="recipe-master-title">Recipe Master</h1>
          <p className="home-sub">Every product's current recipe, its cost per kg and whether it meets its recipe standards. Open a product to set its standards.</p>
        </div>
      </div>
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      {!state.loading && !state.error && (
        <div className="home-stats page-kpis">
          <div className="h-stat kpi-card tone-brand">
            <KpiIcon name="recipes" />
            <span className="h-stat-label">Recipes</span>
            <span className="h-stat-value">{kpis.recipes}</span>
            <span className="h-stat-note">{kpis.ingredients} ingredients in all</span>
          </div>
          <div className="h-stat kpi-card tone-teal">
            <KpiIcon name="analyzed" />
            <span className="h-stat-label">Fully costed</span>
            <span className="h-stat-value">{kpis.costed}<small className="h-stat-of"> / {kpis.recipes}</small></span>
            <span className="h-stat-note">every ingredient has a cost</span>
          </div>
          <div className={`h-stat kpi-card ${kpis.ruled && kpis.met < kpis.ruled ? "tone-act" : "tone-pos"}`}>
            <KpiIcon name="questions" />
            <span className="h-stat-label">Standards met</span>
            <span className="h-stat-value">{kpis.met}<small className="h-stat-of"> / {kpis.ruled}</small></span>
            <span className="h-stat-note">{kpis.ruled ? "recipes with standards" : "no standards set yet"}</span>
          </div>
        </div>
      )}
      <div className="sv-toolbar">
        <div className="chip-filters" role="tablist" aria-label="Show">
          {FILTERS.map(([k, label, f]) => (
            <button key={k} type="button" role="tab" aria-selected={filter === k} className={`chip-filter${filter === k ? " is-on" : ""}`} onClick={() => setFilter(k)}>
              {label}<span className="chip-count">{all.filter(f).length}</span>
            </button>
          ))}
        </div>
        <input className="input search sv-search" type="search" placeholder="Search product, SKU or category" value={q}
          onChange={(e) => setQ(e.target.value)} aria-label="Search recipes" />
      </div>
      {state.loading ? <p className="muted">Loading…</p> : rows.length === 0 ? (
        <div className="panel sv-empty"><KpiIcon name="recipes" /><strong>No recipes here</strong><span className="muted">Try another filter or search.</span></div>
      ) : (
        <div className="rm-grid">
          {rows.map((r) => {
            const costed = r.ingredients ? Math.round((100 * (r.ingredients - (r.missing_costs || 0))) / r.ingredients) : 0;
            return (
              <article key={r.product_id} className={`rm-card accent-${accentOf(r.category_name)}`}>
                <header className="rm-head">
                  <span className="rm-avatar" aria-hidden="true">{(r.product_name || "?")[0]}</span>
                  <div className="rm-title">
                    <strong>{r.product_name}</strong>
                    <span className="muted">{[r.category_name, r.sku].filter(Boolean).join(" · ")}</span>
                  </div>
                  {r.has_recipe && <span className="rm-version">v{r.version || 1}</span>}
                </header>
                <dl className="rm-stats">
                  <div><dt>Ingredients</dt><dd>{r.ingredients}</dd></div>
                  <div><dt>Cost per kg</dt><dd>{money(r.cost_per_kg)}</dd></div>
                  <div><dt>Files</dt><dd>{r.attachments}</dd></div>
                </dl>
                {r.has_recipe && (
                  <div className="rm-costing">
                    <span className="rm-costing-label">
                      <span>Costing</span>
                      <span className={r.missing_costs ? "rm-warn" : "rm-ok"}>
                        {r.missing_costs ? `${r.missing_costs} of ${r.ingredients} without a cost` : "complete"}
                      </span>
                    </span>
                    <span className="rm-bar" aria-hidden="true"><span style={{ width: `${costed}%` }} /></span>
                  </div>
                )}
                <footer className="rm-foot">
                  {standardsChip(r)}
                  {onOpenProduct && (
                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => onOpenProduct(r.product_id)}>Open</button>
                  )}
                </footer>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
