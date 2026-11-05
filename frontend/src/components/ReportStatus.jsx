import { useEffect, useState } from "react";
import { errorText, REPORT_LABELS, REPORT_TONES, reportHistory, submitReport } from "../services/nextApi";

/* The survey person's view of their report's review (requirements point 9):
   status, the admin's note if returned, and "Submit for Admin review". */
export default function ReportStatus({ responseId, status: initial, analyzed }) {
  const [state, setState] = useState({ status: initial, history: [] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!responseId) return;
    reportHistory(responseId).then((h) => setState({ status: h.report_status, history: h.history || [] })).catch(() => {});
  }, [responseId]);
  const status = state.status;
  const returned = [...state.history].reverse().find((h) => h.to === "returned");
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const h = await submitReport(responseId);
      setState({ status: h.report_status, history: h.history || [] });
    } catch (err) {
      setError(errorText(err, "The report couldn't be submitted."));
    } finally {
      setBusy(false);
    }
  };
  if (!analyzed && !status) return null;
  return (
    <div className="report-status">
      <span className={`status-chip is-${REPORT_TONES[status] || "neutral"}`}>{REPORT_LABELS[status] || "Report not ready yet"}</span>
      {status === "returned" && returned?.note && <span className="muted">Admin's note: “{returned.note}”</span>}
      {(status === "generated" || status === "returned") && (
        <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={submit}>
          {busy ? "Submitting…" : status === "returned" ? "Submit again" : "Submit for Admin review"}
        </button>
      )}
      {error && <span className="form-error" role="alert">{error}</span>}
    </div>
  );
}
