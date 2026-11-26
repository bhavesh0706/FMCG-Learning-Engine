import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "../config";
import { generatePasskey, passkeyProblem } from "../services/recordingCrypto";
import Modal from "./Modal";

/*
  The recording passkey chain (backend api/recordings.py):
    Super Admin  sets the ONE passkey (Products page) and sends it to Admins   -> OrgPasskeyButton (popup)
    Admin        sees it once, then passes it on to survey persons (Team page) -> AdminPasskeyPanel
    Survey person enters it to play the recordings of their own surveys (ProtectedRecording)
  The Super Admin and Admins play recordings without it. Only its hash is stored; a sent passkey
  is shown to the recipient once and then deleted from the server.
*/

const api = axios.create({ baseURL: API_BASE_URL });
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const reason = (err, fallback) => {
  const d = err?.response?.data?.detail;
  return typeof d === "string" ? d : d?.message || fallback;
};
const copy = (text) => navigator.clipboard?.writeText(text).catch(() => {});

function useLoad(url) {
  const [state, setState] = useState({ loading: true, data: null, error: "" });
  const load = useCallback(() => {
    api.get(url).then((r) => setState({ loading: false, data: r.data, error: "" }))
      .catch((e) => setState({ loading: false, data: null, error: reason(e, "This couldn't be loaded.") }));
  }, [url]);
  useEffect(() => { load(); }, [load]);
  return [state, load];
}

function Note({ note }) {
  if (!note) return null;
  return <p className={note.tone === "ok" ? "alert alert-ok" : "form-error"} role={note.tone === "ok" ? "status" : "alert"}>{note.text}</p>;
}

/* Super Admin, Products page: set or change the passkey, and send it to Admins. */
export function OrgPasskeyCard() {
  const [state, reload] = useLoad("/org/recording-passkey");
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [fresh, setFresh] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  if (state.loading || state.error) return null;
  const status = state.data;
  const admins = status.admins || [];
  const run = async (work, ok) => {
    setBusy(true);
    setNote(null);
    try {
      await work();
      setNote({ tone: "ok", text: ok });
      reload();
    } catch (e) {
      setNote({ tone: "error", text: reason(e, "That didn't work. Try again.") });
    } finally {
      setBusy(false);
    }
  };
  const save = () => run(async () => {
    await api.put("/org/recording-passkey", { passkey: draft.trim() });
    setFresh(draft.trim());
    setDraft("");
    setOpen(false);
  }, "Passkey saved. Send it to your Admins now: it's shown only this once.");
  const send = () => run(() => api.post("/org/recording-passkey/share", { passkey: fresh, user_ids: admins.map((a) => a.user_id) }),
    `Passkey sent to ${plural(admins.length, "Admin")}. Each sees it once, then passes it on to their survey persons.`);
  return (
    <section className="panel org-passkey" aria-labelledby="org-passkey-title">
      <div className="org-passkey-head">
        <div>
          <h2 id="org-passkey-title" className="org-panel-title">Recording passkey <span className="status-chip is-neutral">For survey persons</span></h2>
          <p className="muted">
            {status.has_passkey ? `Set · ends in “…${status.hint}”. ` : "Not set yet. "}
            You and your Admins play recordings without it. Survey persons need it to play the recordings of their own surveys:
            you send it to your Admins, and they pass it on.
          </p>
        </div>
        {!open && <button type="button" className="btn btn-primary btn-sm" onClick={() => setOpen(true)}>{status.has_passkey ? "Change passkey" : "Set passkey"}</button>}
      </div>
      <Note note={note} />
      {open && (
        <>
          <div className="passkey-row">
            <input className="input passkey-input" value={draft} autoComplete="off" spellCheck={false} autoFocus aria-label="Recording passkey"
              placeholder="Generate one, or type at least 8 characters" onChange={(e) => setDraft(e.target.value)} />
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setDraft(generatePasskey())}>Generate</button>
            <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={busy || Boolean(passkeyProblem(draft))}>Save</button>
            <button type="button" className="link-btn" onClick={() => { setOpen(false); setDraft(""); }}>Cancel</button>
          </div>
          {draft && passkeyProblem(draft) && <p className="form-error small">{passkeyProblem(draft)}</p>}
        </>
      )}
      {fresh && (
        <div className="passkey-once" role="status">
          <span>New passkey (shown only now): <code>{fresh}</code></span>
          <span className="org-tl-actions">
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => copy(fresh)}>Copy</button>
            <button type="button" className="btn btn-primary btn-sm" onClick={send} disabled={busy || !admins.length}
              title={admins.map((a) => a.name).join(", ")}>Send to {plural(admins.length, "Admin")}</button>
            <button type="button" className="link-btn" onClick={() => setFresh("")}>Hide</button>
          </span>
        </div>
      )}
    </section>
  );
}

/* Super Admin, Products page: one header button ("Set passkey" / "Change passkey") that opens the
   recording passkey in a popup: set or change it, then copy it and send it to the Admins. */
export function OrgPasskeyButton() {
  const [state, reload] = useLoad("/org/recording-passkey");
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [fresh, setFresh] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  if (state.loading || state.error) return null;
  const status = state.data;
  const admins = status.admins || [];
  const close = () => { setOpen(false); setDraft(""); setFresh(""); setNote(null); };
  const run = async (work, ok) => {
    setBusy(true);
    setNote(null);
    try {
      await work();
      setNote({ tone: "ok", text: ok });
      reload();
    } catch (e) {
      setNote({ tone: "error", text: reason(e, "That didn't work. Try again.") });
    } finally {
      setBusy(false);
    }
  };
  const save = () => run(async () => {
    await api.put("/org/recording-passkey", { passkey: draft.trim() });
    setFresh(draft.trim());
    setDraft("");
  }, "Passkey saved. Send it to your Admins now: it's shown only this once.");
  const send = () => run(() => api.post("/org/recording-passkey/share", { passkey: fresh, user_ids: admins.map((a) => a.user_id) }),
    `Passkey sent to ${plural(admins.length, "Admin")}. Each sees it once, then passes it on to their survey persons.`);
  return (
    <>
      <button type="button" className="btn btn-secondary" onClick={() => setOpen(true)}>
        {status.has_passkey ? "Change passkey" : "Set passkey"}
      </button>
      {open && (
        <Modal title={status.has_passkey ? "Change recording passkey" : "Set recording passkey"} onClose={close}
          subtitle={`${status.has_passkey ? `Current passkey ends in “…${status.hint}”. ` : "Not set yet. "}Survey persons need it to play the recordings of their own surveys; you and your Admins don't.`}
          footer={(
            <>
              <button type="button" className="btn btn-secondary" onClick={close}>{fresh ? "Done" : "Cancel"}</button>
              {!fresh && <button type="button" className="btn btn-primary" onClick={save} disabled={busy || Boolean(passkeyProblem(draft))}>Save passkey</button>}
            </>
          )}>
          <Note note={note} />
          {!fresh ? (
            <label className="field">
              <span className="field-label">New passkey</span>
              <span className="passkey-row">
                <input className="input passkey-input" value={draft} autoComplete="off" spellCheck={false} autoFocus aria-label="Recording passkey"
                  placeholder="Generate one, or type at least 8 characters" onChange={(e) => setDraft(e.target.value)} />
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setDraft(generatePasskey())}>Generate</button>
              </span>
              {draft && passkeyProblem(draft) && <span className="form-error small">{passkeyProblem(draft)}</span>}
            </label>
          ) : (
            <div className="passkey-once" role="status">
              <span>New passkey (shown only now): <code>{fresh}</code></span>
              <span className="org-tl-actions">
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => copy(fresh)}>Copy</button>
                <button type="button" className="btn btn-primary btn-sm" onClick={send} disabled={busy || !admins.length}
                  title={admins.map((a) => a.name).join(", ")}>Send to {plural(admins.length, "Admin")}</button>
              </span>
            </div>
          )}
        </Modal>
      )}
    </>
  );
}

/* Admin, Team page: see the passkey the Super Admin sent (once) and pass it on to survey persons. */
export function AdminPasskeyPanel() {
  const [state, reload] = useLoad("/admin/recording-passkey/people");
  const [passkey, setPasskey] = useState("");
  const [chosen, setChosen] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  if (state.loading || state.error) return null;
  const d = state.data;
  const reveal = async () => {
    setNote(null);
    try {
      const { data } = await api.post("/recordings/passkey/reveal");
      setPasskey(data.passkey);
      setNote({ tone: "ok", text: "This is the passkey from the Super Admin. It's shown only once: send it to your survey persons below, or note it safely." });
      reload();
    } catch (e) {
      setNote({ tone: "error", text: reason(e, "The passkey couldn't be shown.") });
    }
  };
  const send = async () => {
    setBusy(true);
    setNote(null);
    try {
      const { data } = await api.post("/admin/recording-passkey/share", { passkey: passkey.trim(), user_ids: [...chosen] });
      setNote({ tone: "ok", text: `Passkey sent to ${plural(data.sent, "survey person")}. Each sees it once when they play a recording.` });
      setChosen(new Set());
      reload();
    } catch (e) {
      setNote({ tone: "error", text: reason(e, "The passkey couldn't be sent.") });
    } finally {
      setBusy(false);
    }
  };
  const toggle = (id) => setChosen((c) => {
    const next = new Set(c);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  return (
    <section className="panel org-passkey" aria-labelledby="admin-passkey-title">
      <div className="org-passkey-head">
        <div>
          <h2 id="admin-passkey-title" className="org-panel-title">Recording passkey for survey persons</h2>
          <p className="muted">
            {d.has_passkey
              ? "Survey persons need it to play the recordings of their own surveys. You play recordings without it."
              : "The Super Admin hasn't set the passkey yet."}
          </p>
        </div>
        {d.passkey_waiting_for_me && <button type="button" className="btn btn-primary btn-sm" onClick={reveal}>Show the passkey sent to me</button>}
      </div>
      <Note note={note} />
      {d.has_passkey && (
        <>
          <div className="passkey-row">
            <input className="input passkey-input" value={passkey} autoComplete="off" spellCheck={false} aria-label="Recording passkey"
              placeholder="The passkey from the Super Admin" onChange={(e) => setPasskey(e.target.value)} />
          </div>
          <ul className="rec-admins">
            {d.people.map((p) => (
              <li key={p.user_id}>
                <label className="member-check">
                  <input type="checkbox" checked={chosen.has(p.user_id)} onChange={() => toggle(p.user_id)} />
                  <span>{p.name}{p.share_pending && <span className="status-chip is-warn">not opened yet</span>}</span>
                </label>
              </li>
            ))}
            {!d.people.length && <li className="muted">No active survey persons.</li>}
          </ul>
          <div>
            <button type="button" className="btn btn-primary btn-sm" onClick={send} disabled={busy || !passkey.trim() || !chosen.size}>
              {busy ? "Sending…" : `Send to ${plural(chosen.size, "survey person")}`}
            </button>
          </div>
        </>
      )}
    </section>
  );
}