import { useEffect, useRef, useState } from "react";
import {
  createFirstAdmin,
  forgetAccount,
  getSetupStatus,
  recentAccounts,
  signIn,
  takeSignOutReason,
} from "../services/auth";

/*
  Web sign-in. Same accounts and rules as the mobile app:
  mobile number (10 digits, +91 allowed) or email, and password.
  While no admin exists, it shows "Set up your workspace" to create the first admin.
*/

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function identifierProblem(id) {
  const v = id.trim();
  if (!v) return "Enter your mobile number or email.";
  if (v.includes("@")) return EMAIL.test(v) ? "" : "Enter a valid email address.";
  let digits = v.replace(/\D/g, "");
  if (digits.length === 12 && digits.startsWith("91")) digits = digits.slice(2);
  return digits.length === 10 ? "" : "Enter a 10-digit mobile number or an email address.";
}

const passwordRule = (p) =>
  p.length < 8 || !/[A-Za-z]/.test(p) || !/\d/.test(p) ? "Use at least 8 characters with letters and numbers." : "";

function Feature({ icon, title, text }) {
  return (
    <li className="login-feature">
      <span className="login-feature-icon" aria-hidden="true">{icon}</span>
      <span>
        <strong>{title}</strong>
        <span>{text}</span>
      </span>
    </li>
  );
}

export default function LoginPage({ apiBase, onSignedIn }) {
  const [mode, setMode] = useState("loading"); // loading | login | setup
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(() => takeSignOutReason());
  const [forgot, setForgot] = useState(false);
  const firstField = useRef(null);
  const passwordField = useRef(null);
  const [recent, setRecent] = useState(recentAccounts);

  const pickAccount = (a) => {
    setIdentifier(a.identifier);
    setPassword("");
    setError("");
    setTimeout(() => passwordField.current?.focus(), 0);
  };

  useEffect(() => {
    let alive = true;
    getSetupStatus(apiBase)
      .then((s) => alive && setMode(s?.needs_setup ? "setup" : "login"))
      .catch(() => alive && setMode("login"));
    return () => {
      alive = false;
    };
  }, [apiBase]);

  useEffect(() => {
    if (mode !== "loading") firstField.current?.focus();
  }, [mode]);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setNotice("");
    const problem =
      (mode === "setup" && name.trim().length < 2 && "Enter your full name.") ||
      identifierProblem(identifier) ||
      (!password && "Enter your password.") ||
      (mode === "setup" && passwordRule(password)) ||
      (mode === "setup" && confirm !== password && "The passwords don't match.") ||
      "";
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    try {
      const user =
        mode === "setup"
          ? await createFirstAdmin(apiBase, name, identifier, password)
          : await signIn(apiBase, identifier, password);
      onSignedIn(user);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const setup = mode === "setup";

  return (
    <div className="login-page">
      {/* Brand side */}
      <aside className="login-brand" aria-hidden={false}>
        <div className="login-brand-top">
          <span className="login-logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="22" height="22"><path d="M8 5v14l11-7z" fill="currentColor" /></svg>
          </span>
          <span className="login-brand-name">FMCG Video Learning Engine</span>
        </div>
        <div className="login-brand-body">
          <h2>Customer feedback, from the field to insight.</h2>
          <p>Surveys taken on the phone, recordings analyzed by AI, and one always-current report for every product.</p>
          <ul className="login-features">
            <Feature icon="🎙️" title="Field surveys" text="Voice or video feedback in English and Indian languages" />
            <Feature icon="✨" title="AI analysis" text="Transcripts, ratings and what customers liked or disliked" />
            <Feature icon="📊" title="Product reports" text="Updated automatically after every new survey" />
          </ul>
        </div>
        <p className="login-brand-foot">Admins manage products and the team. Survey persons take surveys.</p>
        <span className="login-orb login-orb-a" aria-hidden="true" />
        <span className="login-orb login-orb-b" aria-hidden="true" />
      </aside>

      {/* Form side */}
      <main className="login-main">
        <form className="login-card" onSubmit={submit} noValidate aria-labelledby="login-title">
          {mode === "loading" ? (
            <div className="login-loading"><span className="spinner" aria-hidden="true" /> Loading…</div>
          ) : (
            <>
              <div className="login-head">
                <h1 id="login-title">{setup ? "Set up your workspace" : "Welcome back"}</h1>
                <p>
                  {setup
                    ? "Create the admin account. You'll add products and your survey team next."
                    : "Sign in with the mobile number or email your admin gave you."}
                </p>
              </div>

              {notice && <p className="login-notice" role="status">{notice}</p>}

              {setup && (
                <label className="login-field">
                  <span>Full name</span>
                  <input ref={firstField} className="login-input" value={name} autoComplete="name"
                    onChange={(e) => setName(e.target.value)} placeholder="e.g. Priya Nair" />
                </label>
              )}

              <label className="login-field">
                <span>Mobile number or email</span>
                <input ref={setup ? undefined : firstField} className="login-input" value={identifier}
                  inputMode="email" autoComplete="username"
                  onChange={(e) => setIdentifier(e.target.value)} placeholder="98765 43210 or name@company.com" />
              </label>

              <div className="login-field">
                <span className="login-field-row">
                  <label htmlFor="login-password">Password</label>
                  {!setup && (
                    <button type="button" className="login-link" aria-expanded={forgot} onClick={() => setForgot((f) => !f)}>
                      Forgot password?
                    </button>
                  )}
                </span>
                <span className="login-password">
                  <input id="login-password" ref={passwordField} className="login-input" type={show ? "text" : "password"} value={password}
                    autoComplete={setup ? "new-password" : "current-password"}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={setup ? "8+ characters, letters and numbers" : "Your password"} />
                  <button type="button" className="login-eye" onClick={() => setShow((s) => !s)}
                    aria-label={show ? "Hide password" : "Show password"}>
                    {show ? "Hide" : "Show"}
                  </button>
                </span>
              </div>

              {forgot && !setup && (
                <p className="login-help" role="note">
                  Ask your admin to reset it: they can set a new password for you on the <strong>Team</strong> page.
                </p>
              )}

              {setup && (
                <label className="login-field">
                  <span>Confirm password</span>
                  <input className="login-input" type={show ? "text" : "password"} value={confirm}
                    autoComplete="new-password" onChange={(e) => setConfirm(e.target.value)} />
                </label>
              )}

              {error && <p className="login-error" role="alert">{error}</p>}

              <button type="submit" className="login-submit" disabled={busy}>
                {busy ? <span className="spinner login-spinner" aria-hidden="true" /> : null}
                {busy ? (setup ? "Creating…" : "Signing in…") : setup ? "Create admin account" : "Sign in"}
              </button>

              <p className="login-foot">
                {setup
                  ? "This setup appears only once, before the first admin exists."
                  : "Survey persons use the same login as in the mobile app."}
              </p>

              {!setup && recent.length > 0 && (
                <div className="login-recent">
                  <p className="login-recent-title">Recently used on this device</p>
                  <ul className="login-recent-grid">
                    {recent.map((a) => (
                      <li key={a.identifier} className={identifier === a.identifier ? "is-on" : ""}>
                        <button type="button" className="login-recent-pick" onClick={() => pickAccount(a)}
                          aria-label={`Sign in as ${a.name}, ${a.role}`}>
                          <span className="login-recent-avatar" aria-hidden="true">{(a.name || "?").trim()[0]?.toUpperCase()}</span>
                          <span className="login-recent-text">
                            <strong>{a.name}</strong>
                            <span>{a.role}</span>
                          </span>
                        </button>
                        <button type="button" className="login-recent-forget" aria-label={`Remove ${a.name} from this device`}
                          title="Remove from this device"
                          onClick={() => {
                            forgetAccount(a.identifier);
                            setRecent(recentAccounts());
                          }}>
                          ×
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </form>
      </main>
    </div>
  );
}