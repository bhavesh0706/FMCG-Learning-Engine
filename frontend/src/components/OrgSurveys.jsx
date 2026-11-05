import { useCallback, useEffect, useState } from "react";
import { getFeedbackById } from "../services/feedbackApi";
import { errorText, getOrgSurvey, listOrgSurveys, REPORT_LABELS, REPORT_TONES, reviewOrgSurvey } from "../services/nextApi";

/* Super Admin: every survey in the application with its report and review status
   (requirements point 9). "Mark reviewed" for reports forwarded by an admin. */
const FILTERS = [["", "All surveys"], ["forwarded", "Forwarded to me"], ["reviewed", "Reviewed"], ["pending_admin", "Pending Admin review"]];

function when(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";
}

/* One survey, the way an Admin sees it: the full analysis page (read-only), with the
   Super Admin's review panel above it. */
function SurveyPage({ id, onBack, onChanged }) {
  const [page, setPage] = useState({ Detail: null, record: null, error: "" });
  const [feedbackId, setFeedbackId] = useState(null);
  useEffect(() => {
    if (!feedbackId) return;
    let alive = true;
    Promise.all([import("../App"), getFeedbackById(feedbackId)])
      .then(([mod, record]) => alive && setPage({ Detail: mod.FeedbackDetail, record, error: "" }))
      .catch((err) => alive && setPage({ Detail: null, record: null, error: errorText(err, "The analysis couldn't be loaded.") }));
    return () => {
      alive = false;
    };
  }, [feedbackId]);
  const { Detail: FeedbackDetail, record } = page;
  return (
    <section className="feedback-detail">
      <button type="button" className="back-link" onClick={onBack}>‹ Surveys</button>
      <div className="panel org-survey-review">
        <Detail id={id} onChanged={onChanged} onFeedback={setFeedbackId} compact />
      </div>
      {page.error && <p className="form-error" role="alert">{page.error}</p>}
      {FeedbackDetail && record && <FeedbackDetail record={record} readOnly onRecordUpdated={() => {}} />}
      {feedbackId && !record && !page.error && <p className="muted">Loading the analysis…</p>}
    </section>
  );
}

function Detail({ id, onChanged, onFeedback, compact = false }) {
  const [d, setD] = useState(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    getOrgSurvey(id).then((x) => { setD(x); onFeedback?.(x.feedback_id || null); })
      .catch((err) => setError(errorText(err, "Couldn't load the survey.")));
  }, [id, onFeedback]);
  if (!d) return <p className="muted">{error || "Loading…"}</p>;
  const review = async () => {
    setBusy(true);
    try {
      const res = await reviewOrgSurvey(id, note.trim() || null);
      setD({ ...d, report_status: res.report_status, history: res.history });
      onChanged();
    } catch (err) {
      setError(errorText(err, "Couldn't mark it reviewed."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="pc-detail">
      <p><strong>Customer:</strong> {[d.customer, d.city, d.country].filter(Boolean).join(", ")} ·{" "}
        {[d.respondent_type, d.audience && (d.audience === "internal" ? "Internal" : "External"), d.occupation, d.sec_class].filter(Boolean).join(" · ") || "No details"}</p>
      <p><strong>Survey #{d.response_id} · {d.product_name}</strong>{" "}
        <span className={`status-chip is-${REPORT_TONES[d.report_status] || "neutral"}`}>{REPORT_LABELS[d.report_status] || (d.status === "analyzed" ? "Analyzed" : "Not analyzed yet")}</span></p>
      {!compact && d.analysis ? (
        <>
          <p><strong>Rating:</strong> {d.analysis.rating != null ? `${Number(d.analysis.rating).toFixed(1)} / 5` : "—"} · <strong>Sentiment:</strong> {d.analysis.sentiment || "—"}</p>
          {d.analysis.summary && <p><strong>Summary:</strong> {d.analysis.summary}</p>}
        </>
      ) : !d.analysis ? <p className="muted">Not analyzed yet.</p> : null}
      {d.answers?.length > 0 && (
        <ul className="pc-history">{d.answers.map((a, i) => <li key={i}><strong>{a.question}</strong> {a.value || "—"}</li>)}</ul>
      )}
      {!compact && d.analysis?.transcript && <p className="pc-transcript">{d.analysis.transcript}</p>}
      {d.history?.length > 0 && (
        <ul className="pc-history">{d.history.map((h, i) => <li key={i}>{when(h.at)} · {h.by}: {h.label}{h.note ? ` · “${h.note}”` : ""}</li>)}</ul>
      )}
      {d.report_status === "forwarded" && (
        <div className="pc-actions">
          <input className="input" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={review}>Mark reviewed</button>
        </div>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}

export default function OrgSurveys() {
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(null); // survey opened as a full page
  const [state, setState] = useState({ loading: true, error: "", surveys: [], counts: {} });
  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true }));
    try {
      const res = await listOrgSurveys(filter ? { report_status: filter } : {});
      setState({ loading: false, error: "", surveys: res.surveys || [], counts: res.counts || {} });
    } catch (err) {
      setState((s) => ({ ...s, loading: false, error: errorText(err, "Surveys couldn't be loaded.") }));
    }
  }, [filter]);
  useEffect(() => { load(); }, [load]);
  if (page) return <SurveyPage id={page} onBack={() => setPage(null)} onChanged={load} />;
  const needle = q.trim().toLowerCase();
  const rows = state.surveys.filter((s) => !needle || [s.product_name, s.executive_name, s.customer, s.city, s.country].join(" ").toLowerCase().includes(needle));
  return (
    <section className="pc" aria-labelledby="org-surveys-title">
      <div className="home-head">
        <div className="page-heading">
          <h1 id="org-surveys-title">Surveys</h1>
          <p>Every survey in the application, by every survey person, with its report and review status.</p>
        </div>
      </div>
      <div className="segmented page-tabs" role="tablist" aria-label="Survey filter">
        {FILTERS.map(([key, label]) => (
          <button key={key || "all"} type="button" role="tab" aria-selected={filter === key} className={filter === key ? "is-on" : ""} onClick={() => setFilter(key)}>
            {label}{key === "forwarded" && state.counts.forwarded ? <span className="seg-count"> {state.counts.forwarded}</span> : null}
            {key === "" && state.counts.total != null ? <span className="seg-count"> {state.counts.total}</span> : null}
          </button>
        ))}
      </div>
      <input className="input search" type="search" placeholder="Search product, survey person, customer, city" value={q} onChange={(e) => setQ(e.target.value)} />
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      {state.loading ? <p className="muted">Loading…</p> : rows.length === 0 ? (
        <div className="panel pc-empty"><strong>No surveys</strong></div>
      ) : (
        <div className="pc-list">
          {rows.map((s) => (
            <article key={s.response_id} className="panel pc-card">
              <header className="pc-head">
                <div>
                  <strong>Survey #{s.response_id} · {s.product_name}</strong>
                  <span className="muted">{[s.category_name, s.executive_name && `by ${s.executive_name}`, [s.city, s.country].filter(Boolean).join(", "), when(s.created_at)].filter(Boolean).join(" · ")}</span>
                </div>
                <span className={`status-chip is-${REPORT_TONES[s.report_status] || "neutral"}`}>{REPORT_LABELS[s.report_status] || (s.status === "analyzed" ? "Analyzed" : "Not analyzed yet")}</span>
              </header>
              <p className="pc-line">{s.rating != null && <strong>{Number(s.rating).toFixed(1)} / 5 · </strong>}{s.summary || <span className="muted">No report yet.</span>}</p>
              <button type="button" className="link-btn" onClick={() => { setPage(s.response_id); window.scrollTo(0, 0); }}>
                Open survey and report
              </button>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
