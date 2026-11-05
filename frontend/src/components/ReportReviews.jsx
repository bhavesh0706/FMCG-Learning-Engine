import { useCallback, useEffect, useState } from "react";
import { approveReport, errorText, listReportReviews, REPORT_LABELS, REPORT_TONES, returnReport } from "../services/nextApi";

/* Admin: survey reports submitted by survey persons (requirements point 9).
   Approve = forward to the Super Admin; Return = back to the survey person with a note. */
const FILTERS = [["pending_admin", "Pending review"], ["forwarded", "Forwarded"], ["returned", "Returned"], ["reviewed", "Reviewed"]];

function when(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "";
}

function ReviewCard({ r, onDone }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (approve) => {
    if (!approve && !note.trim()) return setError("Add a note so the survey person knows what to fix.");
    setBusy(true);
    setError("");
    try {
      await (approve ? approveReport(r.response_id, note.trim() || null) : returnReport(r.response_id, note.trim()));
      onDone();
    } catch (err) {
      setError(errorText(err, "The decision couldn't be saved."));
      setBusy(false);
    }
  };
  return (
    <article className="panel pc-card">
      <header className="pc-head">
        <div>
          <strong>Survey #{r.response_id} · {r.product_name}</strong>
          <span className="muted">{[r.customer, r.city, r.executive_name && `by ${r.executive_name}`, when(r.created_at)].filter(Boolean).join(" · ")}</span>
        </div>
        <span className={`status-chip is-${REPORT_TONES[r.report_status] || "neutral"}`}>{REPORT_LABELS[r.report_status] || r.report_label}</span>
      </header>
      <p className="pc-line">
        {r.rating != null && <strong>{Number(r.rating).toFixed(1)} / 5 · </strong>}
        {r.sentiment && <span className="muted">{r.sentiment} · </span>}
        {r.summary || <span className="muted">No summary.</span>}
      </p>
      {r.report_status === "pending_admin" && (
        <div className="pc-actions">
          <input className="input" placeholder="Note (required to return)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => act(true)}>Approve and forward</button>
          <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => act(false)}>Return with note</button>
        </div>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
    </article>
  );
}

export default function ReportReviews({ onCounts }) {
  const [filter, setFilter] = useState("pending_admin");
  const [state, setState] = useState({ loading: true, error: "", reports: [], counts: {} });
  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: "" }));
    try {
      const res = await listReportReviews(filter);
      setState({ loading: false, error: "", reports: res.reports || [], counts: res.counts || {} });
      onCounts?.(res.counts || {});
    } catch (err) {
      setState((s) => ({ ...s, loading: false, error: errorText(err, "Report reviews couldn't be loaded.") }));
    }
  }, [filter, onCounts]);
  useEffect(() => { load(); }, [load]);
  return (
    <section className="pc" aria-label="Report reviews">
      <p className="muted pc-intro">Survey persons submit each survey's report for review. Approve to forward it to the Super Admin, or return it with a note.</p>
      <div className="segmented page-tabs" role="tablist" aria-label="Report review filter">
        {FILTERS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={filter === key} className={filter === key ? "is-on" : ""} onClick={() => setFilter(key)}>
            {label}{key === "pending_admin" && state.counts.pending_admin != null && <span className="seg-count"> {state.counts.pending_admin}</span>}
          </button>
        ))}
      </div>
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      {state.loading ? <p className="muted">Loading…</p> : state.reports.length === 0 ? (
        <div className="panel pc-empty"><strong>{filter === "pending_admin" ? "No reports waiting for review" : "Nothing here yet"}</strong></div>
      ) : (
        <div className="pc-list">{state.reports.map((r) => <ReviewCard key={r.response_id} r={r} onDone={load} />)}</div>
      )}
    </section>
  );
}
