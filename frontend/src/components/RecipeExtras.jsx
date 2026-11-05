import { useState } from "react";
import { downloadAttachment, errorText, removeAttachment, uploadAttachment } from "../services/nextApi";

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

export function StandardsCard({ standards }) {
  if (!standards) return null;
  const results = standards.results || [];
  return (
    <section className="recipe-card" aria-labelledby="standards-title">
      <header className="recipe-card-head">
        <div>
          <h3 id="standards-title">Recipe standards</h3>
          <p className="muted">Each recipe is checked against the standard rules set in the Recipe Master.</p>
        </div>
        {results.length > 0 && (
          <span className={`status-chip ${standards.passed ? "is-ok" : "is-neg"}`}>
            {standards.passed ? "All standards met" : `${standards.failed_count} not met`}
          </span>
        )}
      </header>
      {results.length === 0 ? (
        <p className="muted">No standard rules apply yet.</p>
      ) : (
        <ul className="std-results is-check">
          {results.map((r) => (
            <li key={r.rule_id} className={r.passed ? "is-ok" : "is-neg"}>
              <span aria-hidden="true">{r.passed ? "✓" : "✕"}</span>
              <span><strong>{r.name}</strong><span className="muted"> · {r.detail}</span></span>
            </li>
          ))}
        </ul>
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
