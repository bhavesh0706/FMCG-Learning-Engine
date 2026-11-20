import { useState } from "react";
import { downloadPlannedReport, errorText, planReport } from "../services/nextApi";

/*
  Prompt -> preview -> download, for Excel and PowerPoint (one product or many).
  The AI only plans what goes into the file; every number comes from the surveys.
  An empty prompt gives the standard report.
*/
const EXAMPLES = {
  pptx: ["Executive summary in 5 slides", "Only complaints, with recipe recommendations", "Compare all surveys", "Only retailers, by city"],
  xlsx: ["Every response with its answers", "Only concerns and recommended actions", "Compare all surveys", "Breakdown by respondent type and city"],
};

async function blobError(err, fallback) {
  if (err?.response?.data instanceof Blob) {
    try { return JSON.parse(await err.response.data.text()).detail || fallback; } catch { return fallback; }
  }
  return errorText(err, fallback);
}

// PowerPoint requests start with this design brief (backend: custom_reports.DESIGN_BRIEF);
// the person adds what they need after it. The brief alone gives the standard deck.
export const DESIGN_BRIEF = "Act as a senior UI/UX designer and create a clear, visually engaging PowerPoint presentation: ";

export default function ReportPrompt({ format, productIds = [], round = null, onDone, autoFocus = true, disabled = false }) {
  const brief = format === "pptx" ? DESIGN_BRIEF : "";
  const [prompt, setPrompt] = useState(brief);
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const kind = format === "pptx" ? "PowerPoint" : "Excel";

  const prepare = async (e) => {
    e?.preventDefault();
    setBusy("plan");
    setError("");
    setPlan(null);
    try {
      setPlan(await planReport({ prompt: prompt.trim(), format, product_ids: productIds, round }));
    } catch (err) {
      setError(errorText(err, "The report couldn't be prepared."));
    } finally {
      setBusy("");
    }
  };
  const download = async () => {
    setBusy("file");
    setError("");
    try {
      await downloadPlannedReport(plan.request_id, format);
      onDone?.();
    } catch (err) {
      setError(await blobError(err, "The download failed."));
    } finally {
      setBusy("");
    }
  };

  return (
    <form className="report-prompt" onSubmit={prepare}>
      <label className="field">
        <span className="field-label">What should the {kind} contain?</span>
        {brief && <span className="muted small">Write your request after the design brief; with the brief alone you get the standard deck.</span>}
        <textarea className="input" rows={3} value={prompt} autoFocus={autoFocus} maxLength={2000}
          placeholder={`e.g. ${EXAMPLES[format][0]}. Leave empty for the standard report.`}
          onFocus={(e) => { const end = e.target.value.length; e.target.setSelectionRange(end, end); }}
          onChange={(e) => { setPrompt(e.target.value); setPlan(null); }} />
      </label>
      <div className="prompt-chips" aria-label="Examples">
        {EXAMPLES[format].map((x) => (
          <button key={x} type="button" className="master-chip" onClick={() => { setPrompt(brief + x); setPlan(null); }}>{x}</button>
        ))}
      </div>
      {plan && (
        <div className="prompt-plan" role="status">
          <strong>{plan.surveys} {plan.surveys === 1 ? "survey" : "surveys"} · {plan.summary}</strong>
          {plan.planned_by === "keywords" && <span className="muted">The AI was unavailable, so the request was read from its keywords.</span>}
          {plan.warnings?.map((w) => <span key={w} className="prompt-warn">{w}</span>)}
        </div>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="pc-actions">
        {!plan ? (
          <button type="submit" className="btn btn-primary" disabled={!!busy || disabled}>{busy === "plan" ? "Preparing…" : `Prepare ${kind}`}</button>
        ) : (
          <>
            <button type="button" className="btn btn-primary" disabled={!!busy || !plan.surveys} onClick={download}>
              {busy === "file" ? "Building the file…" : `Download ${kind}`}
            </button>
            <button type="button" className="btn btn-secondary" disabled={!!busy} onClick={() => setPlan(null)}>Change request</button>
          </>
        )}
      </div>
    </form>
  );
}