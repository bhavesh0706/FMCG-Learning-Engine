import { lovLabel } from "../services/lov";
import { useEffect, useState } from "react";
import Modal from "./Modal";
import { applyQuestionTemplate, createQuestionTemplate, listQuestionTemplates, questionSourceProducts } from "../services/catalogAdminApi";

/* Reusable survey questions (Add project > Survey questions):
   - UseTemplateDialog: a saved template or an earlier product's questions, previewed, then copied into
     this product as its own questions (replacing or adding to the current ones). The template and the
     other product never change; {product} in a template reads this product's name.
   - SaveTemplateDialog: this product's current questions saved as a new template for future projects. */

const TYPE = { rating: "Rating 1–5", single_choice: "Single choice", multiple_choice: "Multiple choice", yes_no: "Yes / No",
  text: "Text answer", preference: "Preference (A vs B)", number: "Number" };
const errorText = (e, fallback) => {
  const d = e?.response?.data?.detail;
  return typeof d === "string" ? d : d?.message || fallback;
};

export function UseTemplateDialog({ product, currentCount, onClose, onApplied }) {
  const [tab, setTab] = useState("templates");
  const [templates, setTemplates] = useState(null);
  const [products, setProducts] = useState(null);
  const [chosen, setChosen] = useState(null); // {kind, item}
  const [replace, setReplace] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    listQuestionTemplates(product.category_id).then((d) => setTemplates(d?.templates || [])).catch(() => setTemplates([]));
    questionSourceProducts(product.id).then((d) => setProducts(d?.products || [])).catch(() => setProducts([]));
  }, [product.id, product.category_id]);
  const list = tab === "templates" ? templates : products;
  const preview = (chosen?.item?.questions || []).map((q) => ({ ...q, text: String(q.text || "").replaceAll("{product}", product.name) }));
  const total = (replace ? 0 : currentCount) + preview.length;
  const use = async () => {
    setBusy(true);
    setError("");
    try {
      await applyQuestionTemplate({ product_id: product.id, replace,
        ...(chosen.kind === "templates" ? { template_id: chosen.item.id } : { from_product_id: chosen.item.id }) });
      onApplied(`${preview.length} questions ${replace ? "now replace the earlier ones" : "added"}: edit, remove or add questions below.`);
    } catch (e) {
      setError(errorText(e, "The questions couldn't be copied. Try again."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="Use existing question template" size="wide" onClose={onClose}
      subtitle={`The questions are copied into ${product.name}: change them freely, the template stays as it is.`}
      footer={(
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={use} disabled={!chosen || busy || total > 10}>
            {busy ? "Copying…" : "Use these questions"}
          </button>
        </>
      )}>
      <div className="segmented" role="tablist" aria-label="Take the questions from">
        {[["templates", "Saved templates"], ["products", "Earlier products"]].map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? "is-active" : ""}
            onClick={() => { setTab(k); setChosen(null); }}>{label}</button>
        ))}
      </div>
      <div className="qt-grid">
        <ul className="qt-list" aria-label={tab === "templates" ? "Saved templates" : "Earlier products"}>
          {list == null && <li className="muted">Loading…</li>}
          {list?.length === 0 && (
            <li className="muted">{tab === "templates" ? "No saved templates yet: save a question set with Save as template." : "No other product has survey questions yet."}</li>
          )}
          {(list || []).map((item) => (
            <li key={item.id}>
              <button type="button" className={`qt-item${chosen?.item?.id === item.id && chosen?.kind === tab ? " is-chosen" : ""}`}
                onClick={() => setChosen({ kind: tab, item })}>
                <strong>{item.name}</strong>
                <span className="muted small">{item.question_count} questions{item.category_name ? ` · ${item.category_name}` : ""}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="qt-preview">
          {!chosen ? <p className="muted">Choose a {tab === "templates" ? "template" : "product"} to see its questions.</p> : (
            <>
              <ol>
                {preview.map((q, i) => (
                  <li key={i}><span>{q.text}</span> <span className="status-chip is-neutral">{lovLabel("QUESTION_TYPE", q.type_code || q.question_type, TYPE[q.question_type] || q.question_type)}</span>
                    {Array.isArray(q.options) && q.options.length > 0 && <span className="muted small"> · {q.options.join(" / ")}</span>}
                  </li>
                ))}
              </ol>
              <fieldset className="qt-mode">
                <label><input type="radio" checked={replace} onChange={() => setReplace(true)} /> Replace the current questions{currentCount ? ` (${currentCount})` : ""}</label>
                <label><input type="radio" checked={!replace} onChange={() => setReplace(false)} disabled={!currentCount} /> Add to the current questions</label>
              </fieldset>
              {total > 10 && <p className="form-error">That makes {total} questions: a product can have at most 10. Choose Replace.</p>}
            </>
          )}
          {error && <p className="form-error" role="alert">{error}</p>}
        </div>
      </div>
    </Modal>
  );
}

export function SaveTemplateDialog({ product, onClose, onSaved }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await createQuestionTemplate({ name: name.trim(), description: description.trim() || null, from_product_id: product.id });
      onSaved(`Saved as the template “${name.trim()}”. Use it for future projects with Use existing question template.`);
    } catch (e) {
      setError(errorText(e, "The template couldn't be saved. Try again."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="Save as question template" onClose={onClose}
      subtitle={`The current questions of ${product.name} are saved for future projects. The product's name in them becomes {product} and reads the new product's name when used.`}
      footer={(
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={save} disabled={busy || name.trim().length < 2}>{busy ? "Saving…" : "Save template"}</button>
        </>
      )}>
      <label className="field">
        <span className="field-label">Template name <span className="req" aria-hidden="true">*</span></span>
        <input className="input" value={name} maxLength={150} autoFocus placeholder="e.g. Snack taste test" onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Description</span>
        <textarea className="input" rows={2} value={description} maxLength={2000} placeholder="Optional: when to use it" onChange={(e) => setDescription(e.target.value)} />
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}