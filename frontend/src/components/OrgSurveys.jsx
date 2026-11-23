import { useCallback, useEffect, useMemo, useState } from "react";
import KpiIcon from "./KpiIcon";
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

const PAGE = 20; // surveys shown at a time

// Rating badge colour: green from 4, amber from 3, red below.
const ratingTone = (r) => (r == null ? "none" : r >= 4 ? "good" : r >= 3 ? "mid" : "low");
// The report's overall sentiment (ai_service: Praise / Suggestion / Mixed / Neutral / Question).
const SENTIMENT = {
  praise: ["Praise", "is-ok"], positive: ["Positive", "is-ok"], suggestion: ["Suggestion", "is-act"], mixed: ["Mixed", "is-act"],
  complaint: ["Complaint", "is-neg"], negative: ["Negative", "is-neg"], neutral: ["Neutral", "is-neutral"], question: ["Question", "is-neutral"],
};
const POSITIVE = new Set(["praise", "positive"]);
const initials = (name) => (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");

export default function OrgSurveys() {
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(null); // survey opened as a full page
  const [shown, setShown] = useState(PAGE); // the list grows by PAGE with "Show more"
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
  useEffect(() => { setShown(PAGE); }, [filter, q]);
  const kpis = useMemo(() => {
    const rated = state.surveys.filter((s) => s.rating != null);
    const withMood = state.surveys.filter((s) => s.sentiment);
    const positive = withMood.filter((s) => POSITIVE.has(String(s.sentiment).toLowerCase())).length;
    return {
      total: state.surveys.length,
      analyzed: rated.length,
      avg: rated.length ? rated.reduce((a, s) => a + Number(s.rating), 0) / rated.length : null,
      positive: withMood.length ? Math.round((100 * positive) / withMood.length) : null,
      people: new Set(state.surveys.map((s) => s.executive_name).filter(Boolean)).size,
    };
  }, [state.surveys]);
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
      {!state.loading && !state.error && (
        <div className="home-stats page-kpis">
          <div className="h-stat kpi-card tone-brand">
            <KpiIcon name="responses" />
            <span className="h-stat-label">Surveys</span>
            <span className="h-stat-value">{kpis.total}</span>
            <span className="h-stat-note">by {kpis.people} survey {kpis.people === 1 ? "person" : "persons"}</span>
          </div>
          <div className="h-stat kpi-card tone-teal">
            <KpiIcon name="analyzed" />
            <span className="h-stat-label">Analyzed</span>
            <span className="h-stat-value">{kpis.analyzed}</span>
            <span className="h-stat-note">{kpis.total - kpis.analyzed ? `${kpis.total - kpis.analyzed} still in analysis` : "all have a report"}</span>
          </div>
          <div className="h-stat kpi-card tone-act">
            <KpiIcon name="rating" />
            <span className="h-stat-label">Average rating</span>
            <span className="h-stat-value">{kpis.avg == null ? "—" : kpis.avg.toFixed(1)}</span>
            <span className="h-stat-note">out of 5</span>
          </div>
          <div className="h-stat kpi-card tone-pos">
            <KpiIcon name="sentiment" />
            <span className="h-stat-label">Praise</span>
            <span className="h-stat-value">{kpis.positive == null ? "—" : `${kpis.positive}%`}</span>
            <span className="h-stat-note">of consumers mainly praised the product</span>
          </div>
        </div>
      )}
      <div className="sv-toolbar">
        <div className="chip-filters" role="tablist" aria-label="Survey filter">
          {FILTERS.map(([key, label]) => (
            <button key={key || "all"} type="button" role="tab" aria-selected={filter === key} className={`chip-filter${filter === key ? " is-on" : ""}`} onClick={() => setFilter(key)}>
              {label}
              {key === "" && state.counts.total != null && <span className="chip-count">{state.counts.total}</span>}
              {key === "forwarded" && state.counts.forwarded ? <span className="chip-count is-alert">{state.counts.forwarded}</span> : null}
            </button>
          ))}
        </div>
        <input className="input search sv-search" type="search" placeholder="Search product, survey person, customer, city" value={q}
          onChange={(e) => setQ(e.target.value)} aria-label="Search surveys" />
      </div>
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      {state.loading ? <p className="muted">Loading…</p> : rows.length === 0 ? (
        <div className="panel sv-empty"><KpiIcon name="responses" /><strong>No surveys here</strong><span className="muted">Try another filter or search.</span></div>
      ) : (
        <>
        <p className="muted small pc-count">
          Showing {Math.min(shown, rows.length)} of {rows.length} {rows.length === 1 ? "survey" : "surveys"}, newest first
        </p>
        <div className="sv-grid">
          {rows.slice(0, shown).map((s) => {
            const mood = SENTIMENT[String(s.sentiment || "").toLowerCase()];
            const open = () => { setPage(s.response_id); window.scrollTo(0, 0); };
            return (
              <article key={s.response_id} className={`sv-card rating-${ratingTone(s.rating)}`}>
                <div className="sv-rating" aria-label={s.rating != null ? `Rating ${Number(s.rating).toFixed(1)} out of 5` : "Not rated yet"}>
                  <strong>{s.rating != null ? Number(s.rating).toFixed(1) : "—"}</strong>
                  <span>{s.rating != null ? "/ 5" : "no rating"}</span>
                </div>
                <div className="sv-body">
                  <header className="sv-head">
                    <div className="sv-title">
                      <strong>{s.product_name}</strong>
                      <span className="sv-id">Survey #{s.response_id}</span>
                    </div>
                    <span className={`status-chip is-${REPORT_TONES[s.report_status] || "neutral"}`}>{REPORT_LABELS[s.report_status] || (s.status === "analyzed" ? "Analyzed" : "Not analyzed yet")}</span>
                  </header>
                  <div className="sv-tags">
                    {mood && <span className={`status-chip ${mood[1]}`}>{mood[0]}</span>}
                    {s.recipe_version && <span className="sv-tag">Recipe v{s.recipe_version}</span>}
                    {s.category_name && <span className="sv-tag">{s.category_name}</span>}
                  </div>
                  <p className="sv-summary">{s.summary || <span className="muted">The report appears here once the recording is analyzed.</span>}</p>
                  <footer className="sv-foot">
                    <span className="sv-person">
                      <span className="sv-avatar" aria-hidden="true">{initials(s.executive_name)}</span>
                      <span>
                        <strong>{s.executive_name || "Survey person"}</strong>
                        <span className="muted">{[[s.city, s.country].filter(Boolean).join(", "), when(s.created_at)].filter(Boolean).join(" · ")}</span>
                      </span>
                    </span>
                    <button type="button" className="btn btn-secondary btn-sm sv-open" onClick={open}>Open report</button>
                  </footer>
                </div>
              </article>
            );
          })}
        </div>
        {rows.length > shown && (
          <div className="pc-more">
            <button type="button" className="btn btn-secondary" onClick={() => setShown((n) => n + PAGE)}>
              Show {Math.min(PAGE, rows.length - shown)} more
            </button>
          </div>
        )}
        </>
      )}
    </section>
  );
}
