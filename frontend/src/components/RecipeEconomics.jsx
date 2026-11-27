import { useEffect, useRef, useState } from "react";
import { compareRecipe, getRecipeRecommendations, saveCommercials } from "../services/catalogAdminApi";

/* Recipe economics, used while adding a new recipe version (all figures come from the server):
- RecommendationsPanel: the latest consumer report's recipe recommendations; Apply puts the
     proposed quantity into the draft, Reject records the decision; either can be changed again.
- CostComparison: the current version against the draft (ingredient changes, batch cost, cost per
     kg, gross profit and margin at the selling price), the commercial targets, the warnings, and the
     approval a version needs when it breaks the cost limit or the minimum margin. */

const rs = (v) => (v == null ? "—" : `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`);
const kg = (v) => (v == null ? "—" : `${+Number(v).toFixed(3)} kg`);
const pct = (v) => (v == null ? "—" : `${v}%`);
// a rise in cost is bad (red); a rise in margin is good (green)
const delta = (v, unit = "rs", upIsGood = false) => {
  if (v == null || Number(v) === 0) return <span className="muted">no change</span>;
  const up = Number(v) > 0;
  const good = up === upIsGood;
  const text = unit === "rs" ? rs(Math.abs(v)) : unit === "pp" ? `${Math.abs(v)} pts` : kg(Math.abs(v));
  return <span className={good ? "econ-down" : "econ-up"}>{up ? "+" : "−"}{text}</span>;
};

export function RecommendationsPanel({ productId, decisions, onDecide, onApply }) {
  const [state, setState] = useState({ loading: true, items: [] });
  useEffect(() => {
    let alive = true;
    getRecipeRecommendations(productId)
      .then((d) => alive && setState({ loading: false, items: d?.recommendations || [], responses: d?.responses || 0 }))
      .catch(() => alive && setState({ loading: false, items: [] }));
    return () => { alive = false; };
  }, [productId]);
  if (state.loading || !state.items.length) return null;
  const decisionOf = (r) => decisions.find((d) => d.change === r.change)?.decision;
  return (
    <section className="recipe-card econ-panel" aria-labelledby="econ-recs-title">
      <h3 id="econ-recs-title">Consumer recommendations</h3>
      <p className="muted small">From the latest consumer report ({state.responses} responses). Apply, reject or change each one before releasing the new version.</p>
      <ul className="econ-recs">
        {state.items.map((r) => {
          const d = decisionOf(r);
          return (
            <li key={r.id} className={`econ-rec${d ? ` is-${d}` : ""}`}>
              <div>
                <strong>{r.change}</strong>
                <span className="muted small">{r.reason ? ` · ${r.reason}` : ""}{r.customers ? ` · ${r.customers} customer${r.customers === 1 ? "" : "s"}` : ""}</span>
                {r.applicable
                  ? <p className="small">{r.ingredient}: {kg(r.current_kg)} → <strong>{kg(r.proposed_kg)}</strong></p>
                  : <p className="small muted">{r.reason && !r.ingredient ? "" : ""}{r.ingredient ? `${r.ingredient}: ` : ""}No quantity to apply: change the recipe yourself if you accept it.</p>}
              </div>
              <span className="econ-rec-actions">
                {d ? (
                  <>
                    <span className={`status-chip ${d === "accepted" ? "is-ok" : "is-neutral"}`}>{d === "accepted" ? "Accepted" : "Rejected"}</span>
                    <button type="button" className="link-btn" onClick={() => onDecide(r, null)}>Undo</button>
                  </>
                ) : (
                  <>
                    <button type="button" className="btn btn-primary btn-sm" onClick={() => { if (r.applicable) onApply(r); onDecide(r, "accepted"); }}>
                      {r.applicable ? "Apply" : "Accept"}
                    </button>
                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => onDecide(r, "rejected")}>Reject</button>
                  </>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function TargetsEditor({ productId, targets, onSaved }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({});
  const [busy, setBusy] = useState(false);
  const start = () => {
    setF({ selling_price_per_kg: targets?.selling_price_per_kg ?? "", target_cost_per_kg: targets?.target_cost_per_kg ?? "", min_margin_pct: targets?.min_margin_pct ?? "" });
    setOpen(true);
  };
  const num = (v) => (String(v).trim() === "" ? null : Number(v));
  const save = async () => {
    setBusy(true);
    try {
      await saveCommercials(productId, { selling_price_per_kg: num(f.selling_price_per_kg), target_cost_per_kg: num(f.target_cost_per_kg), min_margin_pct: num(f.min_margin_pct) });
      setOpen(false);
      onSaved();
    } finally {
      setBusy(false);
    }
  };
  if (!open) {
    return (
      <div className="econ-targets">
        <span>Selling price <strong>{rs(targets?.selling_price_per_kg)}/kg</strong></span>
        <span>Cost limit <strong>{rs(targets?.target_cost_per_kg)}/kg</strong></span>
        <span>Minimum margin <strong>{pct(targets?.min_margin_pct)}</strong></span>
        <button type="button" className="link-btn" onClick={start}>Edit targets</button>
      </div>
    );
  }
  const field = (key, label) => (
    <label className="field">
      <span className="field-label">{label}</span>
      <input className="input" inputMode="decimal" value={f[key]} onChange={(e) => setF({ ...f, [key]: e.target.value })} />
    </label>
  );
  return (
    <div className="econ-targets-edit">
      {field("selling_price_per_kg", "Selling price (₹ per kg)")}
      {field("target_cost_per_kg", "Cost limit (₹ per kg)")}
      {field("min_margin_pct", "Minimum margin (%)")}
      <span className="econ-rec-actions">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setOpen(false)}>Cancel</button>
        <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save targets"}</button>
      </span>
    </div>
  );
}

export function CostComparison({ productId, recipe, fromVersion, approve, onApprove, onResult, saved = null }) {
  const [cmp, setCmp] = useState(saved);
  const [error, setError] = useState("");
  const [all, setAll] = useState(false);
  const [tick, setTick] = useState(0);
  const timer = useRef(null);
  useEffect(() => {
    if (saved) return undefined;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      compareRecipe(productId, recipe)
        .then((d) => { setCmp(d); setError(""); onResult?.(d); })
        .catch(() => setError("The cost comparison couldn't be calculated right now."));
    }, 400);
    return () => clearTimeout(timer.current);
  }, [productId, JSON.stringify(recipe?.ingredients), tick, saved]); // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <p className="form-error">{error}</p>;
  if (!cmp) return null;
  const rows = all ? cmp.rows : cmp.rows.filter((r) => r.status !== "same");
  const o = cmp.old, n = cmp.new;
  return (
    <section
      className="recipe-card econ-panel rc-tone rc-costing"
      aria-labelledby="econ-cmp-title"
    >
      <header className="recipe-card-head">
        <h3 id="econ-cmp-title">
          {saved
            ? `Cost compared with recipe v${fromVersion}`
            : `Cost comparison · v${fromVersion} → new version`}
        </h3>
      </header>
      {!saved && <TargetsEditor productId={productId} targets={cmp.targets} onSaved={() => setTick((t) => t + 1)} />}
      <div className="table-wrap">
        <table className="data-table econ-table">
          <thead>
            <tr><th scope="col">Ingredient</th><th scope="col">Quantity</th><th scope="col">Cost per KG</th><th scope="col">Cost in Use</th><th scope="col">Batch cost</th><th scope="col">Change</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ingredient} className={`is-${r.status}`}>
                <td><strong>{r.ingredient}</strong>{r.status === "added" ? " (new)" : r.status === "removed" ? " (removed)" : ""}</td>
                <td>{r.old_kg === r.new_kg ? kg(r.new_kg) : <>{kg(r.old_kg)} → <strong>{kg(r.new_kg)}</strong></>}</td>
                <td>{r.old_cost_per_kg === r.new_cost_per_kg ? rs(r.new_cost_per_kg) : <>{rs(r.old_cost_per_kg)} → <strong>{rs(r.new_cost_per_kg)}</strong></>}</td>
                <td>{rs(r.old_cost_in_use)} → <strong>{rs(r.new_cost_in_use)}</strong></td>
                <td>{rs(r.old_batch_cost)} → <strong>{rs(r.new_batch_cost)}</strong></td>
                <td>{delta(r.batch_cost_change)}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={6} className="muted">No ingredient changed yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {cmp.rows.some((r) => r.status === "same") && (
        <div className="econ-toggle">
          <button type="button" className="link-btn" onClick={() => setAll(!all)}>{all ? "Show changed ingredients only" : "Show all ingredients"}</button>
        </div>
      )}
      <div className="econ-totals">
        <div><span className="muted small">Total batch cost</span><strong>{rs(o.batch_cost)} → {rs(n.batch_cost)}</strong>{delta(cmp.change.batch_cost)}</div>
        <div><span className="muted small">Product cost per kg</span><strong>{rs(o.cost_per_kg)} → {rs(n.cost_per_kg)}</strong>{delta(cmp.change.cost_per_kg)}</div>
      </div>
      {cmp.warnings.length > 0 && (
        <ul className="econ-warnings" role="alert">
          {cmp.warnings.map((w) => <li key={w.code} className={w.code === "costs_incomplete" ? "is-info" : ""}>{w.message}</li>)}
        </ul>
      )}
      {!saved && cmp.needs_approval && (
        <label className="econ-approve">
          <input type="checkbox" checked={approve} onChange={(e) => onApprove(e.target.checked)} />
          <span>I have reviewed the cost and profit impact: release this version anyway.</span>
        </label>
      )}
    </section>
  );
}