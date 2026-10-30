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

// One sign-in for every role; "Signing in as" only adapts the wording. The account's
// role on the server always decides the dashboard, so picking the wrong one is harmless.
const ROLE_COPY = {
  executive: {
    tab: "Survey person",
    title: "Sign in to take surveys",
    sub: "Use the mobile number and password your admin gave you.",
    label: "Mobile number",
    placeholder: "98765 43210",
    inputMode: "tel",
    forgot: <>Ask your admin to reset it on the <strong>Team</strong> page, then sign in with the new password.</>,
    foot: "No account yet? Ask your admin to add you. The mobile app uses the same login.",
  },
  admin: {
    tab: "Admin",
    title: "Admin sign-in",
    sub: "Manage products, your survey team and reports.",
    label: "Email or mobile number",
    placeholder: "name@company.com",
    inputMode: "email",
    forgot: <>Ask another admin to set a new password for you. If you're the only admin, contact whoever runs your server.</>,
    foot: "Admin accounts are created at setup or by another admin.",
  },
  client: {
    tab: "Super Admin",
    title: "Super Admin sign-in",
    sub: "See your consumer reports and manage your products, recipes and rounds.",
    label: "Work email",
    placeholder: "insights@company.com",
    inputMode: "email",
    forgot: <>Contact the research team. They'll give you a one-time password; you choose your own the first time you sign in.</>,
    foot: "Your Super Admin login is created by the research team.",
  },
};
// The brand panel (left) speaks to the person signing in. Every claim matches what the
// product does for that role.
const ROLE_BRAND = {
  executive: {
    headline: "Every survey, captured right in the field.",
    line: "Pick the product, ask the questions, record the customer; the app does the rest.",
    features: [
      ["⚡", "Quick surveys", "Product, customer and questions in a few taps"],
      ["☁️", "Works offline", "Surveys upload by themselves when you're back online"],
      ["🎙️", "Any language", "Customers answer in English or Indian languages"],
    ],
    foot: "Your surveys are analyzed automatically by AI.",
  },
  admin: {
    headline: "Your products, your team, one live view.",
    line: "Follow every product's feedback, manage your survey team and read the AI reports.",
    features: [
      ["📊", "Live product reports", "Updated automatically after every analyzed survey"],
      ["👥", "Team management", "Add survey persons and choose what they survey"],
      ["⚖️", "Compare recipe rounds", "See how consumers reacted to each recipe version"],
    ],
    foot: "You're notified of every product and recipe change by the Super Admin.",
  },
  client: {
    headline: "What consumers really think of your products.",
    line: "Consumer reports for every round, so you can see what each recipe change achieved.",
    features: [
      ["📈", "Consumer reports", "Ratings, sentiment and what consumers liked or disliked"],
      ["🧪", "Recipe versions", "Every recipe change becomes a new version to test"],
      ["⚖️", "Compare rounds", "See whether a change improved the product"],
    ],
    foot: "Reports update automatically as new surveys are analyzed.",
  },
};
const ROLE_KEY = "fmcg.loginRole";
// "Organization" = accounts remembered before the role was renamed Super Admin.
const ROLE_FROM_LABEL = { Admin: "admin", "Super Admin": "client", Organization: "client", "Survey person": "executive" };

function savedRole() {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get("as");
    const asUrl = { admin: "admin", superadmin: "client", "super-admin": "client", organization: "client", org: "client", surveyor: "executive", survey: "executive" }[fromUrl || ""];
    if (asUrl) return asUrl;
    const stored = localStorage.getItem(ROLE_KEY);
    return ROLE_COPY[stored] ? stored : "executive";
  } catch {
    return "executive";
  }
}

function rememberRole(role) {
  try {
    if (ROLE_COPY[role]) localStorage.setItem(ROLE_KEY, role);
  } catch {
    /* private mode: the choice just isn't remembered */
  }
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
  const [role, setRole] = useState(savedRole);
  const copy = ROLE_COPY[role];

  const chooseRole = (r) => {
    setRole(r);
    rememberRole(r);
    setForgot(false);
    setError("");
  };

  const pickAccount = (a) => {
    if (ROLE_FROM_LABEL[a.role]) chooseRole(ROLE_FROM_LABEL[a.role]);
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
      rememberRole(user?.role); // next time the page opens on the account's real role
      onSignedIn(user);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const setup = mode === "setup";
  // The first-time setup creates the admin, so it shows the admin's panel.
  const brand = ROLE_BRAND[setup ? "admin" : role] || ROLE_BRAND.executive;

  return (
    <div className="login-page">
      {/* Brand side */}
      <aside className="login-brand" aria-hidden={false}>
        <div className="login-brand-top">
          <span className="login-logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="20" height="20"><path d="M8 5v14l11-7z" fill="currentColor" /></svg>
          </span>
          <span className="login-brand-name">FMCG Video Learning Engine</span>
        </div>
        <div className="login-brand-body" aria-live="polite">
          <h2>{brand.headline}</h2>
          <p>{brand.line}</p>
          <ul className="login-features">
            {brand.features.map(([icon, title, text]) => (
              <Feature key={title} icon={icon} title={title} text={text} />
            ))}
          </ul>
        </div>
        <p className="login-brand-foot">{brand.foot}</p>
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
              {!setup && (
                <div className="login-roles">
                  <span className="login-roles-label" id="login-roles-label">Signing in as</span>
                  <div className="login-roles-seg" role="radiogroup" aria-labelledby="login-roles-label">
                    {Object.entries(ROLE_COPY).map(([key, c]) => (
                      <button key={key} type="button" role="radio" aria-checked={role === key}
                        className={role === key ? "is-on" : ""} onClick={() => chooseRole(key)}>
                        {c.tab}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="login-head">
                <h1 id="login-title">{setup ? "Set up your workspace" : copy.title}</h1>
                <p>
                  {setup
                    ? "Create the admin account. You'll add products and your survey team next."
                    : copy.sub}
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
                <span>{setup ? "Mobile number or email" : copy.label}</span>
                <input ref={setup ? undefined : firstField} className="login-input" value={identifier}
                  inputMode={setup ? "email" : copy.inputMode} autoComplete="username"
                  onChange={(e) => setIdentifier(e.target.value)}
                  placeholder={setup ? "98765 43210 or name@company.com" : copy.placeholder} />
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
                <p className="login-help" role="note">{copy.forgot}</p>
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
                  : copy.foot}
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