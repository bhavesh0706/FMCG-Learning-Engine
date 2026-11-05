import { useState } from "react";
import Modal from "./Modal";
import ReportPrompt from "./ReportPrompt";
import { downloadReport, errorText } from "../services/nextApi";

/* A product's report: Excel downloads directly; PowerPoint asks what the slides should contain. */
export default function ReportDownloads({ productId, round }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const excel = async () => {
    setBusy(true);
    setError("");
    try {
      await downloadReport(productId, "xlsx", round);
    } catch (err) {
      let text = errorText(err, "The download failed.");
      if (err?.response?.data instanceof Blob) {
        try { text = JSON.parse(await err.response.data.text()).detail || text; } catch { /* keep the generic text */ }
      }
      setError(text);
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="report-downloads">
      <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={excel}>{busy ? "Preparing…" : "Excel"}</button>
      <button type="button" className="btn btn-secondary btn-sm" onClick={() => setOpen(true)}>PowerPoint</button>
      {error && <span className="form-error" role="alert">{error}</span>}
      {open && (
        <Modal title="PowerPoint report" subtitle="Describe what the slides should contain. The numbers always come from the surveys."
          onClose={() => setOpen(false)}>
          <ReportPrompt format="pptx" productIds={[productId]} round={round} onDone={() => setOpen(false)} />
        </Modal>
      )}
    </span>
  );
}