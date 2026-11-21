import { useCallback, useEffect, useState } from "react";
import Modal from "./Modal";
import { addSuperAdmin, listSuperAdmins, orgError, resetSuperAdminPassword, setSuperAdminActive } from "../services/orgApi";

/*
  Super Admins (Account page): every Super Admin login of the organization, add one,
  reset another's password, disable / reactivate. Each change asks for your own password.
  A new or reset login gets a one-time starting password, changed at the first sign-in.
  Stored in the database as users with role "client".
*/

function when(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" }) : "Never";
}

function PasswordConfirm({ value, onChange }) {
  return (
    <label className="field">
      <span className="field-label">Your password <span className="req" aria-hidden="true">*</span></span>
      <input className="input" type="password" autoComplete="current-password" value={value} onChange={(e) => onChange(e.target.value)}
        placeholder="To confirm it's you" />
    </label>
  );
}

function Credentials({ result, onClose }) {
  const [copied, setCopied] = useState(false);
  const text = `Sign in at ${window.location.origin}\nEmail: ${result.user.email}\nStarting password: ${result.password}\nYou'll choose your own password at the first sign-in.`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <Modal title={result.reset ? "New starting password" : "Super Admin added"} subtitle="Shown only now. Send it privately."
      onClose={onClose} footer={<button type="button" className="btn btn-primary" onClick={onClose}>Done</button>}>
      <div className="credentials-box">
        <p><strong>{result.user.name}</strong></p>
        <p>Email: <code>{result.user.email}</code></p>
        <p>Starting password: <code>{result.password}</code></p>
        <p className="muted small">At the first sign-in they must choose their own password; this one then stops working.</p>
      </div>
      <button type="button" className="btn btn-secondary btn-sm" onClick={copy}>{copied ? "Copied" : "Copy sign-in details"}</button>
    </Modal>
  );
}

function AddDialog({ onClose, onAdded }) {
  const [form, setForm] = useState({ name: "", email: "", current_password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (form.name.trim().length < 2) return setError("Enter the full name.");
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email.trim())) return setError("Enter a valid email address.");
    if (!form.current_password) return setError("Enter your password to confirm.");
    setBusy(true);
    setError("");
    try {
      onAdded(await addSuperAdmin({ name: form.name.trim(), email: form.email.trim(), current_password: form.current_password }));
    } catch (err) {
      setError(orgError(err, "The Super Admin couldn't be added."));
      setBusy(false);
    }
  };
  return (
    <Modal title="Add Super Admin" subtitle="They manage products, recipes, standards, survey questions and surveys, and see every response."
      onClose={onClose}
      footer={<>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>{busy ? "Adding…" : "Add Super Admin"}</button>
      </>}>
      <label className="field">
        <span className="field-label">Full name <span className="req" aria-hidden="true">*</span></span>
        <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Priya Shah" />
      </label>
      <label className="field">
        <span className="field-label">Sign-in email <span className="req" aria-hidden="true">*</span></span>
        <input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@company.com" />
      </label>
      <PasswordConfirm value={form.current_password} onChange={(v) => setForm({ ...form, current_password: v })} />
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

function ConfirmWithPassword({ title, text, confirmLabel, danger, onClose, onConfirm }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (!password) return setError("Enter your password to confirm.");
    setBusy(true);
    setError("");
    try {
      await onConfirm(password);
    } catch (err) {
      setError(orgError(err, "That didn't work. Try again."));
      setBusy(false);
    }
  };
  return (
    <Modal title={title} subtitle={text} onClose={onClose}
      footer={<>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={busy} onClick={go}>{busy ? "Working…" : confirmLabel}</button>
      </>}>
      <PasswordConfirm value={password} onChange={setPassword} />
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

export default function SuperAdminsCard() {
  const [state, setState] = useState({ loading: true, error: "", rows: [] });
  const [dialog, setDialog] = useState(null);
  const [notice, setNotice] = useState("");
  const load = useCallback(() => {
    listSuperAdmins()
      .then((r) => setState({ loading: false, error: "", rows: r.super_admins || [] }))
      .catch((err) => setState({ loading: false, error: orgError(err, "Super Admins couldn't be loaded."), rows: [] }));
  }, []);
  useEffect(() => { load(); }, [load]);
  const done = (text) => { setDialog(null); setNotice(text); load(); };
  return (
    <section className="panel acct-card super-admins" aria-labelledby="acct-super-admins">
      <div className="acct-card-head super-admins-head">
        <div>
          <h2 id="acct-super-admins">Super Admins</h2>
          <p className="muted">Everyone who can manage this organization's products, recipes, standards, questions and surveys.</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => setDialog({ type: "add" })}>Add Super Admin</button>
      </div>
      {notice && <p className="form-status-ok" role="status">{notice}</p>}
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      {state.loading ? <p className="muted">Loading…</p> : (
        <ul className="admins-list">
          {state.rows.map((u) => (
            <li key={u.id} className={u.is_active ? "" : "is-off"}>
              <span className="admins-who">
                <strong>{u.name}</strong>{u.is_me && <span className="status-chip is-neutral">You</span>}
                <span className="muted">{u.email}</span>
                <span className="muted small">
                  Last sign-in: {when(u.last_login_at)}{u.must_change_password ? " · must choose a password at the next sign-in" : ""}
                </span>
              </span>
              <span className={`status-chip ${u.is_active ? "is-ok" : "is-neg"}`}>{u.is_active ? "Active" : "Disabled"}</span>
              {!u.is_me && (
                <span className="admins-actions">
                  <button type="button" className="link-btn" onClick={() => setDialog({ type: "reset", user: u })}>Reset password</button>
                  <button type="button" className={`link-btn${u.is_active ? " danger-link" : ""}`} onClick={() => setDialog({ type: "toggle", user: u })}>
                    {u.is_active ? "Disable" : "Reactivate"}
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {dialog?.type === "add" && (
        <AddDialog onClose={() => setDialog(null)} onAdded={(result) => { setDialog({ type: "credentials", result }); setNotice(`${result.user.name} was added as a Super Admin.`); load(); }} />
      )}
      {dialog?.type === "reset" && (
        <ConfirmWithPassword title={`Reset ${dialog.user.name}'s password?`} confirmLabel="Reset password"
          text="A new starting password is created and shown once; they're signed out everywhere." onClose={() => setDialog(null)}
          onConfirm={async (pw) => { const r = await resetSuperAdminPassword(dialog.user.id, pw); setDialog({ type: "credentials", result: { ...r, reset: true } }); load(); }} />
      )}
      {dialog?.type === "toggle" && (
        <ConfirmWithPassword title={dialog.user.is_active ? `Disable ${dialog.user.name}?` : `Reactivate ${dialog.user.name}?`}
          confirmLabel={dialog.user.is_active ? "Disable" : "Reactivate"} danger={dialog.user.is_active}
          text={dialog.user.is_active ? "They're signed out everywhere and can't sign in until reactivated." : "They can sign in again with their password."}
          onClose={() => setDialog(null)}
          onConfirm={async (pw) => { await setSuperAdminActive(dialog.user.id, !dialog.user.is_active, pw); done(dialog.user.is_active ? `${dialog.user.name} is disabled.` : `${dialog.user.name} is active again.`); }} />
      )}
      {dialog?.type === "credentials" && <Credentials result={dialog.result} onClose={() => setDialog(null)} />}
    </section>
  );
}
