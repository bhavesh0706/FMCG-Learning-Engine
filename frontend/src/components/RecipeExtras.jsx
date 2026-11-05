import { useCallback, useEffect, useState } from "react";
import { downloadAttachment, errorText, listStandards, removeAttachment, removeStandard, uploadAttachment } from "../services/nextApi";
import StandardRuleForm, { KINDS, ruleCondition } from "./StandardRuleForm";

/*
  Recipe tab extras (requirements points 1-3), from the server's recipe response:
  Costing (Usage %, Cost per KG, Cost in Use), Standards check, Attachments of the
  process, and steps of an earlier version (read-only: new recipes have no steps).
*/

const money = (v) => (v == null ? "—" : `₹${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);
const sizeOf = (b) => (b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round((b || 0) / 1024))} KB`);

export function CostingCard({ costing }) {
  const rows = costing?.ingredients || [];
  if (!rows.length) return null;
  return (
    <section className="recipe-card" aria-labelledby="costing-title">
      <header className="recipe-card-head">
        <div>
          <h3 id="costing-title">Costing</h3>
          <p className="muted">Cost in Use = Cost per KG × Usage % (the ingredient's cost in 1 kg of product).</p>
        </div>
        <span className={`status-chip ${costing.complete ? "is-ok" : "is-warn"}`}>
          {costing.complete ? "Costing complete" : `${costing.missing_costs.length} without a cost`}
        </span>
      </header>
      <div className="table-wrap">
        <table className="data-table ing-table">
          <thead>
            <tr>
              <th scope="col">Ingredient</th>
              <th scope="col" className="num-cell">Usage %</th>
              <th scope="col" className="num-cell">Cost per KG</th>
              <th scope="col" className="num-cell">Cost in Use</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.name}-${i}`}>
                <td><strong>{r.name}</strong></td>
                <td className="num-cell">{r.usage_pct == null ? "—" : `${r.usage_pct}%`}</td>
                <td className="num-cell">{money(r.cost_per_kg)}</td>
                <td className="num-cell">{money(r.cost_in_use)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td><strong>Recipe cost per kg</strong></td>
              <td className="num-cell"><strong>100%</strong></td>
              <td className="num-cell muted">{costing.batch_cost != null ? `Batch: ${money(costing.batch_cost)}` : ""}</td>
              <td className="num-cell"><strong>{money(costing.cost_per_kg)}</strong></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}

export function StandardsCard({ standards, productId, readOnly = true, onChanged }) {
  // Rules made for this product only (Super Admin adds, edits and removes them here)
  const [rules, setRules] = useState([]);
  const [editing, setEditing] = useState(null);
  const [adding, setAdding] = useState(false);
  const loadRules = useCallback(() => {
    if (readOnly || !productId) return;
    listStandards(productId).then((r) => setRules(r.standards || [])).catch(() => {});
  }, [productId, readOnly]);
  useEffect(() => { loadRules(); }, [loadRules]);
  if (!standards) return null;
  const results = standards.results || [];
  const changed = () => { setEditing(null); setAdding(false); loadRules(); onChanged?.(); };
  const remove = async (r) => {
    if (!window.confirm(`Remove the rule “${r.name}”?`)) return;
    await removeStandard(r.id).catch(() => {});
    changed();
  };
  const SCOPE = { product: "This product", category: "Category", all: "All products" };
  return (
    <section className="recipe-card" aria-labelledby="standards-title">
      <header className="recipe-card-head">
        <div>
          <h3 id="standards-title">Recipe standards</h3>
          <p className="muted">This recipe is checked against the rules for this product, its category and all products.</p>
        </div>
        {results.length > 0 && (
          <span className={`status-chip ${standards.passed ? "is-ok" : "is-neg"}`}>
            {standards.passed ? "All standards met" : `${standards.failed_count} not met`}
          </span>
        )}
      </header>
      {results.length === 0 ? (
        <p className="muted">No standard rules apply to this product yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table std-table">
            <thead>
              <tr><th scope="col">Standard</th><th scope="col">Expected</th><th scope="col">Actual</th><th scope="col">Status</th><th scope="col">Reason</th></tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.rule_id}>
                  <td><strong>{r.name}</strong><span className="cell-sub">{SCOPE[r.scope] || ""}</span></td>
                  <td>{r.expected || "—"}</td>
                  <td>{r.actual || "—"}</td>
                  <td><span className={`status-chip ${r.passed ? "is-ok" : "is-neg"}`}>{r.status || (r.passed ? "MET" : "NOT MET")}</span></td>
                  <td className={r.passed ? "muted" : "std-reason"}>{r.reason || r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!readOnly && productId && (
        <div className="std-product-rules">
          <h4>Rules for this product</h4>
          <ul className="std-results">
            {rules.map((r) => editing === r.id ? (
              <li key={r.id} className="is-editing"><StandardRuleForm productId={productId} rule={r} onSaved={changed} onCancel={() => setEditing(null)} /></li>
            ) : (
              <li key={r.id}>
                <span><strong>{r.name}</strong><span className="muted"> · {KINDS[r.kind] || r.kind} · {ruleCondition(r)}</span></span>
                <span className="attach-actions">
                  <button type="button" className="link-btn" onClick={() => setEditing(r.id)}>Edit</button>
                  <button type="button" className="link-btn danger-link" onClick={() => remove(r)}>Remove</button>
                </span>
              </li>
            ))}
            {!rules.length && <li className="muted">No rules for this product only.</li>}
          </ul>
          {adding ? (
            <StandardRuleForm productId={productId} onSaved={changed} onCancel={() => setAdding(false)} />
          ) : (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAdding(true)}>Add a rule for this product</button>
          )}
        </div>
      )}
    </section>
  );
}

export function AttachmentsCard({ productId, attachments = [], readOnly, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pick = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      await uploadAttachment(productId, file);
      onChanged?.();
    } catch (err) {
      setError(errorText(err, "The file couldn't be uploaded."));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (a) => {
    if (!window.confirm(`Remove ${a.filename}?`)) return;
    try {
      await removeAttachment(a.id);
      onChanged?.();
    } catch (err) {
      setError(errorText(err, "The file couldn't be removed."));
    }
  };
  return (
    <section className="recipe-card" aria-labelledby="attach-title">
      <header className="recipe-card-head">
        <div>
          <h3 id="attach-title">Process attachments <span className="count-badge">{attachments.length}</span></h3>
          <p className="muted">Supporting files for the recipe process: specifications, photos, process sheets (PDF, images, Office files, up to 20 MB).</p>
        </div>
        {!readOnly && (
          <label className={`btn btn-secondary btn-sm${busy ? " is-busy" : ""}`}>
            {busy ? "Uploading…" : "Attach a file"}
            <input type="file" hidden disabled={busy} onChange={pick}
              accept=".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.csv,.txt" />
          </label>
        )}
      </header>
      {error && <p className="form-error" role="alert">{error}</p>}
      {attachments.length === 0 ? (
        <p className="muted">No files attached.</p>
      ) : (
        <ul className="attach-list">
          {attachments.map((a) => (
            <li key={a.id}>
              <span className="attach-name"><strong>{a.filename}</strong>
                <span className="muted"> · {sizeOf(a.size_bytes)}{a.recipe_version ? ` · v${a.recipe_version}` : ""}{a.uploaded_by ? ` · ${a.uploaded_by}` : ""}</span>
              </span>
              <span className="attach-actions">
                <button type="button" className="link-btn" onClick={() => downloadAttachment(a).catch((err) => setError(errorText(err, "Download failed.")))}>Download</button>
                {!readOnly && <button type="button" className="link-btn danger-link" onClick={() => remove(a)}>Remove</button>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function LegacyStepsCard({ legacy }) {
  if (!legacy?.steps?.length) return null;
  return (
    <section className="recipe-card is-legacy" aria-labelledby="legacy-title">
      <header className="recipe-card-head">
        <div>
          <h3 id="legacy-title">Steps from version {legacy.version || "1"} <span className="status-chip is-neutral">Read-only</span></h3>
          <p className="muted">New recipe versions don't use steps; describe the process in the notes and attachments.</p>
        </div>
      </header>
      <ol className="process">
        {legacy.steps.map((s, i) => (
          <li key={i}>
            <span className="process-no">{i + 1}</span>
            <div className="process-body">
              <div className="process-title"><strong>{s.title}</strong>
                <span className="process-tags">{[s.temperature, s.duration].filter(Boolean).map((t) => <span key={t} className="ptag">{t}</span>)}</span>
              </div>
              {s.details && <p className="process-details">{s.details}</p>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
