import { useCallback, useEffect, useState } from "react";
import { getProducts, getProductCheck, listProductChecks, reviewProductCheck } from "../services/feedbackApi";

/*
  Survey history > Product checks (admins).
  After each analysis the backend checks whether the customer talked about the
  product the survey person selected (services/product_check.py):
    Mismatch detected  -> kept out of every report until reviewed here
    Unverified         -> no product named (or mixed): counted under the selected product
  Confirm keeps the selected product, Correct moves the survey to the right product,
  Mark invalid keeps it out for good (the recording is kept). Every decision is audited.
*/

const FILTERS = [
  ["pending", "Needs review"],
  ["unverified", "Unverified"],
  ["reviewed", "Reviewed"],
];

const STATUS_CHIP = { mismatch: "is-neg", uncertain: "is-warn", match: "is-ok" };
const REVIEW_LABEL = { confirmed: "Confirmed", corrected: "Corrected", invalid: "Marked invalid" };

function when(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

function errorText(err, fallback) {
  return err?.response?.data?.detail || err?.message || fallback;
}

function Evidence({ evidence }) {
  const mentions = Object.entries(evidence?.mentions || {});
  const words = Object.entries(evidence?.words || {});
  if (!mentions.length && !words.length) return <span className="muted">No product named in the recording.</span>;
  return (
    <span className="pc-evidence">
      {mentions.map(([name, n]) => <span key={`m-${name}`} className="pc-tag">“{name}” ×{n}</span>)}
      {words.map(([name, n]) => <span key={`w-${name}`} className="pc-tag is-weak" title="Words from the product's name">{name} words ×{n}</span>)}
    </span>
  );
}

function CheckCard({ check, products, onDone }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [mode, setMode] = useState(null); // null | "correct"
  const [productId, setProductId] = useState(check.detected_product?.id || "");
  const [remarks, setRemarks] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const reviewed = REVIEW_LABEL[check.review_status];

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !detail) {
      try {
        setDetail(await getProductCheck(check.response_id));
      } catch (err) {
        setError(errorText(err, "Couldn't load the transcript."));
      }
    }
  };

  const act = async (action) => {
    setBusy(true);
    setError("");
    try {
      await reviewProductCheck(check.response_id, { action, remarks, ...(action === "correct" ? { product_id: Number(productId) } : {}) });
      onDone();
    } catch (err) {
      setError(errorText(err, "The review couldn't be saved."));
      setBusy(false);
    }
  };

  return (
    <article className="panel pc-card">
      <header className="pc-head">
        <div>
          <strong>Survey #{check.response_id}</strong>
          <span className="muted">
            {[check.survey.customer, check.survey.executive_name && `by ${check.survey.executive_name}`, when(check.survey.created_at)].filter(Boolean).join(" · ")}
          </span>
        </div>
        <span className={`status-chip ${reviewed ? "is-ok" : STATUS_CHIP[check.status] || ""}`}>{reviewed || check.label}</span>
      </header>

      <dl className="pc-products">
        <div><dt>Selected product</dt><dd>{check.selected_product.name}</dd></div>
        <div aria-hidden="true" className="pc-arrow">→</div>
        <div>
          <dt>Detected in the feedback</dt>
          <dd>{check.detected_product?.name || <span className="muted">Not identified</span>}
            {check.confidence ? <span className="muted"> · {Math.round(check.confidence * 100)}% sure</span> : null}</dd>
        </div>
        {check.corrected_product && <div><dt>Corrected to</dt><dd>{check.corrected_product.name}</dd></div>}
      </dl>
      <p className="pc-line"><span className="muted">Evidence:</span> <Evidence evidence={check.evidence} /></p>
      {check.status === "mismatch" && check.review_status === "pending" && (
        <p className="pc-note">Kept out of {check.selected_product.name}'s report until you review it.</p>
      )}
      {reviewed && (
        <p className="pc-line muted">{reviewed} by {check.reviewed_by || "an admin"} · {when(check.reviewed_at)}{check.remarks ? ` · “${check.remarks}”` : ""}</p>
      )}

      <button type="button" className="link-btn" onClick={toggle} aria-expanded={open}>{open ? "Hide transcript" : "View transcript"}</button>
      {open && (
        <div className="pc-detail">
          {!detail ? <p className="muted">Loading…</p> : (
            <>
              {detail.summary && <p><strong>AI summary:</strong> {detail.summary}</p>}
              <p className="pc-transcript">{detail.transcript || "No transcript."}</p>
              {detail.history?.length > 0 && (
                <ul className="pc-history">
                  {detail.history.map((h) => <li key={h.at}>{when(h.at)} · {h.by}: {h.summary}</li>)}
                </ul>
              )}
            </>
          )}
        </div>
      )}

      {!reviewed && (
        <div className="pc-actions">
          <input className="input" placeholder="Remarks (optional)" value={remarks} onChange={(e) => setRemarks(e.target.value)} />
          {mode === "correct" ? (
            <>
              <select className="input" value={productId} onChange={(e) => setProductId(e.target.value)} aria-label="Correct product">
                <option value="">Choose the product</option>
                {products.filter((p) => p.id !== check.selected_product.id).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <button type="button" className="btn btn-primary btn-sm" disabled={busy || !productId} onClick={() => act("correct")}>Move survey</button>
              <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setMode(null)}>Cancel</button>
            </>
          ) : (
            <>
              <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => act("confirm")}>
                Confirm {check.selected_product.name}
              </button>
              <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => setMode("correct")}>Correct the product</button>
              <button type="button" className="btn btn-secondary btn-sm pc-invalid" disabled={busy} onClick={() => act("invalid")}>Mark invalid</button>
            </>
          )}
        </div>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
    </article>
  );
}

export default function ProductChecks({ onCounts }) {
  const [filter, setFilter] = useState("pending");
  const [state, setState] = useState({ loading: true, error: "", checks: [], counts: {} });
  const [products, setProducts] = useState([]);

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: "" }));
    try {
      const res = await listProductChecks(filter);
      setState({ loading: false, error: "", checks: res.checks || [], counts: res.counts || {} });
      onCounts?.(res.counts || {});
    } catch (err) {
      setState((s) => ({ ...s, loading: false, error: errorText(err, "Product checks couldn't be loaded.") }));
    }
  }, [filter, onCounts]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    getProducts().then((res) => setProducts(res?.products || [])).catch(() => {});
  }, []);

  return (
    <section className="pc" aria-label="Product checks">
      <p className="muted pc-intro">
        After each analysis we check that the customer talked about the product that was selected. Mismatches stay out of the
        reports until you confirm, correct or mark them invalid.
      </p>
      <div className="segmented page-tabs" role="tablist" aria-label="Product check filter">
        {FILTERS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={filter === key} className={filter === key ? "is-on" : ""} onClick={() => setFilter(key)}>
            {label}
            {key !== "reviewed" && state.counts[key] != null && <span className="seg-count"> {state.counts[key]}</span>}
          </button>
        ))}
      </div>
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      {state.loading ? (
        <p className="muted">Loading…</p>
      ) : state.checks.length === 0 ? (
        <div className="panel pc-empty">
          <strong>{filter === "pending" ? "Nothing to review" : filter === "unverified" ? "No unverified surveys" : "No reviews yet"}</strong>
          <span className="muted">{filter === "pending" ? "Every analyzed survey talks about the product that was selected." : ""}</span>
        </div>
      ) : (
        <div className="pc-list">
          {state.checks.map((c) => <CheckCard key={c.response_id} check={c} products={products} onDone={load} />)}
        </div>
      )}
    </section>
  );
}
