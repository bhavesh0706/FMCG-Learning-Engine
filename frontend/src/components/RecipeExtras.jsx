import { setApprovedIngredients } from "../services/catalogAdminApi";
import SectionIcon from "./SectionIcon";
import { confirmDialog } from "./ConfirmDialog";
import { useCallback, useEffect, useState } from "react";
import { downloadAttachment, viewAttachment, errorText, listStandards, removeAttachment, removeStandard, uploadAttachment } from "../services/nextApi";
import StandardRuleForm, { kindLabel, ruleCondition } from "./StandardRuleForm";

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
    <section className="recipe-card rc-tone rc-costing" aria-labelledby="costing-title">
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

/* One titled box inside the Recipe standards card, so the check results, the approved
   ingredients and the rules read as three separate parts. */
function StdBlock({ tone, icon, title, count, hint, aside, children }) {
  return (
    <section className={`std-block tone-${tone}`}>
      <header className="std-block-head">
        <span className="std-block-icon" aria-hidden="true"><SectionIcon name={icon} /></span>
        <div className="std-block-title">
          <h4>{title}{count != null && <span className="count-badge">{count}</span>}</h4>
          {hint && <p>{hint}</p>}
        </div>
        {aside && <div className="std-block-aside">{aside}</div>}
      </header>
      <div className="std-block-body">{children}</div>
    </section>
  );
}

/* The ingredients this product's recipe may use. Once listed, the recipe editor offers only these
   (each once) and the server refuses others. Ingredients already in the recipe can't be removed. */
function ApprovedIngredients({ productId, approved, recipeIngredients, onChanged }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = (n) => String(n || "").trim().toLowerCase();
  const inRecipe = new Set(recipeIngredients.map(key));
  const save = async (list) => {
    setBusy(true);
    setError("");
    try {
      await setApprovedIngredients(productId, list);
      onChanged?.();
    } catch (err) {
      setError(errorText(err, "The approved ingredients couldn't be saved."));
    } finally {
      setBusy(false);
    }
  };
  const add = () => {
    const names = text.split(/[,\n]/).map((n) => n.trim()).filter(Boolean);
    const fresh = names.filter((n) => !approved.some((a) => key(a) === key(n)));
    if (!fresh.length) return setError(names.length ? "Already in the list." : "");
    setText("");
    save([...approved, ...fresh]);
  };
  const missingFromList = recipeIngredients.filter((n) => !approved.some((a) => key(a) === key(n)));
  return (
    <StdBlock tone="ingr" icon="ingredients" title="Approved Ingredients" count={approved.length}
      hint={approved.length ? "The only ingredients the recipe may use, each once: they're offered in the recipe's ingredient dropdown."
        : "No list yet: any ingredient can be typed in the recipe. Add ingredients here to limit the recipe to them."}
      aside={missingFromList.length > 0 && (
        <button type="button" className="link-btn" disabled={busy} onClick={() => save([...approved, ...missingFromList])}>
          + Add the current recipe's {missingFromList.length} ingredient{missingFromList.length === 1 ? "" : "s"}
        </button>
      )}>
    <div className="appr">
      {approved.length > 0 && (
        <ul className="appr-chips">
          {approved.map((n) => (
            <li key={n} className={inRecipe.has(key(n)) ? "is-used" : ""}>
              <span>{n}</span>
              {inRecipe.has(key(n)) && <span className="appr-used" title="Used in the current recipe">In recipe</span>}
              <button type="button" aria-label={`Remove ${n}`} title="Remove" disabled={busy} onClick={async () => {
                if (inRecipe.has(key(n)) && !(await confirmDialog({
                  title: `Remove “${n}”?`,
                  message: "The current recipe uses it: the next recipe version must leave it out.",
                  confirmLabel: "Remove", danger: true,
                }))) return;
                save(approved.filter((a) => a !== n));
              }}>×</button>
            </li>
          ))}
        </ul>
      )}
      <div className="appr-add">
        <input className="input" placeholder="Add ingredients, e.g. Sugar, Salt, Turmeric Powder" value={text} maxLength={500}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy || !text.trim()} onClick={add}>Add</button>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
    </StdBlock>
  );
}

export function StandardsCard({ standards, productId, readOnly = true, onChanged, approved = null, recipeIngredients = [] }) {
  // Rules made for this product only (Super Admin adds, edits and removes them here)
  const [rules, setRules] = useState([]);
  const [editing, setEditing] = useState(null);
  const [adding, setAdding] = useState(false); // false, or the rule type to start with
  const loadRules = useCallback(() => {
    if (readOnly || !productId) return;
    listStandards(productId).then((r) => setRules(r.standards || [])).catch(() => {});
  }, [productId, readOnly]);
  useEffect(() => { loadRules(); }, [loadRules]);
  if (!standards) return null;
  const results = standards.results || [];
  const changed = () => { setEditing(null); setAdding(false); loadRules(); onChanged?.(); };
  const remove = async (r) => {
    if (!(await confirmDialog({ title: `Remove the rule “${r.name}”?`, message: "The recipe is no longer checked against it.", confirmLabel: "Remove", danger: true }))) return;
    await removeStandard(r.id).catch(() => {});
    changed();
  };
  const SCOPE = { product: "This product", category: "Category", all: "All products" };
  return (
    <section className="recipe-card rc-tone rc-standards" aria-labelledby="standards-title">
      <header className="recipe-card-head">
        <div>
          <h3 id="standards-title"><SectionIcon name="standards" />{readOnly ? "Recipe standards" : "Recipe standards for this product"}</h3>
          <p className="muted">Rules the recipe must meet before it can be saved and surveyed.</p>
        </div>
      </header>
      <StdBlock tone="check" icon="standards" title="Check Results" count={results.length || null}
        hint="How the current recipe does against each rule: met or not met, and why."
        aside={results.length > 0 && (
          <span className={`status-chip ${standards.passed ? "is-ok" : "is-neg"}`}>
            {standards.passed ? `All ${results.length} met` : `${standards.failed_count} not met`}
          </span>
        )}>
      {results.length === 0 ? (
        <div className="std-empty"><strong>No standards configured yet</strong><span>Add a rule in Configured Rules: the recipe is then checked against it on every save.</span></div>
      ) : (
        <>
        <div className="table-wrap">
          <table className="data-table std-table">
            <thead>
              <tr><th scope="col">Standard</th><th scope="col">Expected</th><th scope="col">Actual</th><th scope="col">Status</th><th scope="col">Reason</th></tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.rule_id} className={r.passed ? "std-row-ok" : "std-row-fail"}>
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
        </>
      )}
      </StdBlock>
      {!readOnly && productId && approved && (
        <ApprovedIngredients productId={productId} approved={approved} recipeIngredients={recipeIngredients} onChanged={onChanged} />
      )}
      {!readOnly && productId && (
        <StdBlock tone="rules" icon="rules" title="Configured Rules" count={rules.length}
          hint="The rules this product's recipe is checked against. Add, edit or remove them here.">
        <div className="std-product-rules">
          {rules.length === 0 && !adding && <p className="muted small">No rules yet.</p>}
          <ul className="std-results">
            {rules.map((r) => editing === r.id ? (
              <li key={r.id} className="is-editing"><StandardRuleForm productId={productId} rule={r} approved={approved || []} onSaved={changed} onCancel={() => setEditing(null)} /></li>
            ) : (
              <li key={r.id}>
                <span><strong>{r.name}</strong><span className="muted"> · {kindLabel(r)} · {ruleCondition(r)}</span></span>
                <span className="attach-actions">
                  <button type="button" className="link-btn" onClick={() => setEditing(r.id)}>Edit</button>
                  <button type="button" className="link-btn danger-link" onClick={() => remove(r)}>Remove</button>
                </span>
              </li>
            ))}
          </ul>
          {adding ? (
            <StandardRuleForm key={String(adding)} productId={productId} approved={approved || []} initialKind={typeof adding === "string" ? adding : null}
              onSaved={changed} onCancel={() => setAdding(false)} />
          ) : (
            <button type="button" className="btn btn-primary btn-sm std-add" onClick={() => setAdding(true)}>+ Add a rule for this product</button>
          )}
        </div>
        </StdBlock>
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
    if (!(await confirmDialog({ title: `Remove “${a.filename}”?`, message: "The file is deleted from this recipe.", confirmLabel: "Remove", danger: true }))) return;
    try {
      await removeAttachment(a.id);
      onChanged?.();
    } catch (err) {
      setError(errorText(err, "The file couldn't be removed."));
    }
  };
  return (
    <section className="recipe-card rc-tone rc-attach" aria-labelledby="attach-title">
      <header className="recipe-card-head">
        <div>
          <h3 id="attach-title"><SectionIcon name="attach" />Process attachments <span className="count-badge">{attachments.length}</span></h3>
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
                <button type="button" className="icon-btn attach-view" aria-label={`View ${a.filename}`} title="View"
                  onClick={() => viewAttachment(a).catch((err) => setError(errorText(err, "The file couldn't be opened.")))}>
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="3" />
                  </svg>
                </button>
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