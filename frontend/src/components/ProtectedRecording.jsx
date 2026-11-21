import { useEffect, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "../config";
import { getSession } from "../services/auth";
import Modal from "./Modal";

/*
  A consumer recording: encrypted, played only after the recording passkey (one for all products)
  (backend api/recordings.py; the same rule in the mobile app's widgets/protected_recording.dart).

    Admin given access to the product, or the Super Admin: "Play" -> passkey -> the server checks
      it and the role, then streams the recording decrypted on the server (a 10-minute link for
      this recording only; no download, no caching). Every attempt is logged.
    Survey person: never (a note explains why).

  children(src) renders the player once unlocked, so pages keep their own player and timeline.
*/

const api = axios.create({ baseURL: API_BASE_URL });

const reason = (err, fallback) => {
  const d = err?.response?.data?.detail;
  return typeof d === "string" ? d : d?.message || fallback;
};

export default function ProtectedRecording({ feedbackId, isVideo, children }) {
  const role = getSession()?.user?.role;
  const isAdmin = role === "admin" || role === "client"; // no passkey
  const isSurveyPerson = role === "executive"; // own surveys, with the passkey from the Admin
  const [src, setSrc] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [asking, setAsking] = useState(false);

  useEffect(() => {
    setSrc("");
    setError("");
    if (!isAdmin) return undefined;
    // Super Admin and Admins: the player appears straight away (no button, no passkey).
    let alive = true;
    setBusy(true);
    api.post(`/recordings/${feedbackId}/authorise`, {})
      .then(({ data }) => alive && setSrc(`${API_BASE_URL}${data.stream_url}`))
      .catch((err) => alive && setError(reason(err, "The recording couldn't be loaded. Check the connection and refresh.")))
      .finally(() => alive && setBusy(false));
    return () => { alive = false; };
  }, [feedbackId, isAdmin]);

  const open = async (passkey) => {
    const { data } = await api.post(`/recordings/${feedbackId}/authorise`, passkey ? { passkey } : {});
    setSrc(`${API_BASE_URL}${data.stream_url}`);
  };
  const play = async () => {
    if (isSurveyPerson) return setAsking(true);
    setBusy(true);
    setError("");
    try {
      await open();
    } catch (err) {
      setError(reason(err, "The recording couldn't be opened. Check the connection and try again."));
    } finally {
      setBusy(false);
    }
  };

  if (src) return children(src);
  return (
    <div className="protected-rec">
      <span className="protected-rec-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="22" height="22"><rect x="5" y="10.5" width="14" height="10" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="M8 10.5V7.5a4 4 0 018 0v3" fill="none" stroke="currentColor" strokeWidth="1.8" /></svg>
      </span>
      <div className="protected-rec-text">
        <strong>{isVideo ? "Video" : "Audio"} recording</strong>
        <span>{isAdmin ? (busy ? "Loading the recording…" : "Stored encrypted. Every play is logged.")
          : isSurveyPerson ? "Enter the recording passkey from your Admin to play it. Every attempt is logged."
            : "Protected for the customer's privacy."}</span>
        {error && <span className="form-error" role="alert">{error}</span>}
      </div>
      {(isSurveyPerson || (isAdmin && error)) && (
        <button type="button" className="btn btn-primary btn-sm" onClick={play} disabled={busy}>{busy ? "Opening…" : "Play recording"}</button>
      )}
      {asking && <PasskeyDialog onClose={() => setAsking(false)} onSubmit={async (k) => { await open(k); setAsking(false); }} />}
    </div>
  );
}

/* Survey person: the passkey their Admin sent (shown once inside the app) or typed in. */
function PasskeyDialog({ onClose, onSubmit }) {
  const [passkey, setPasskey] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    api.get("/recordings/passkey").then((r) => setWaiting(Boolean(r.data.passkey_waiting))).catch(() => {});
  }, []);

  const submit = async () => {
    if (!passkey.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await onSubmit(passkey.trim());
    } catch (err) {
      setError(reason(err, "The recording couldn't be opened. Check the connection and try again."));
      setBusy(false);
    }
  };
  const reveal = async () => {
    setError("");
    try {
      const { data } = await api.post("/recordings/passkey/reveal");
      setPasskey(data.passkey);
      setShow(true);
      setRevealed(true);
      setWaiting(false);
    } catch (err) {
      setError(reason(err, "The passkey couldn't be shown."));
    }
  };

  return (
    <Modal title="Recording passkey" subtitle="Enter the recording passkey your Admin gave you." onClose={onClose}
      footer={<>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" onClick={submit} disabled={busy || !passkey.trim()}>{busy ? "Checking…" : "Play"}</button>
      </>}>
      {waiting && (
        <button type="button" className="btn btn-secondary passkey-reveal" onClick={reveal}>Show the passkey sent to me</button>
      )}
      {revealed && <p className="alert alert-warn" role="note">Shown once and deleted from the server: keep it somewhere safe.</p>}
      <label className="field">
        <span className="field-label">Passkey</span>
        <span className="login-password">
          <input className="input" type={show ? "text" : "password"} value={passkey} autoComplete="off" spellCheck={false} autoFocus
            onChange={(e) => setPasskey(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} placeholder="e.g. K7QM-9XPD-2RTF-H4WB" />
          <button type="button" className="login-eye" onClick={() => setShow((v) => !v)} aria-label={show ? "Hide passkey" : "Show passkey"}>
            {show ? "Hide" : "Show"}
          </button>
        </span>
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}
