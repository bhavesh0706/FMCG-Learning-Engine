import { useEffect, useState } from "react";
import Modal from "./Modal";
import { addTeamMember, getMemberProducts, resetMemberPassword, saveMemberProducts, signOutMember, updateTeamMember } from "../services/teamApi";
import { confirmDialog } from "./ConfirmDialog";
import { listProducts } from "../services/catalogAdminApi";
import { API_BASE_URL } from "../config";
import Select from "./Select";

/*
  Team: the admin creates each executive's login, then shares it with them.
  The executive logs in to the mobile app with the mobile number and password.
*/

const GENDERS = ["Male", "Female", "Other", "Prefer not to say"];

// Easy to read aloud and type on a phone, e.g. "Mango4821".
const WORDS = ["Mango", "Field", "Survey", "Sunny", "River", "Cedar", "Maple", "Coral", "Amber", "Tiger", "Lotus", "Pearl"];
export function generatePassword() {
  const word = WORDS[Math.floor(Math.random() * WORDS.length)];
  const digits = String(Math.floor(1000 + Math.random() * 9000));
  return `${word}${digits}`;
}

function errorText(err, fallback) {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) {
    const field = d[0]?.loc?.[d[0].loc.length - 1];
    const label = { name: "Name", mobile: "Mobile number", password: "Password", age: "Age" }[field];
    return `${label ? `${label}: ` : ""}${String(d[0].msg).replace("Value error, ", "")}`;
  }
  if (err?.code === "ERR_NETWORK") return `Can't reach the backend at ${API_BASE_URL}.`;
  return fallback;
}

const passwordProblem = (p) =>
  p.length < 8 || !/[A-Za-z]/.test(p) || !/\d/.test(p) ? "Use at least 8 characters with letters and numbers." : "";

function PasswordField({ value, onChange, label = "Password *" }) {
  const [show, setShow] = useState(true);
  return (
    <label className="field">
      <span className="field-label">{label.endsWith(" *") ? <>{label.slice(0, -2)} <span className="req" aria-hidden="true">*</span></> : label}</span>
      <div className="password-row">
        <input className="input" type={show ? "text" : "password"} value={value} autoComplete="new-password"
          onChange={(e) => onChange(e.target.value)} placeholder="8+ letters and numbers" />
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setShow((s) => !s)}>
          {show ? "Hide" : "Show"}
        </button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => onChange(generatePassword())}>
          Generate
        </button>
      </div>
    </label>
  );
}

export function AddMemberDialog({ onClose, onAdded }) {
  const [form, setForm] = useState({
    name: "",
    mobile: "",
    email: "",
    age: "",
    gender: "",
    password: generatePassword(),
    can_view_history: false,
    role: "executive", // executive = survey person (mobile app) · admin = research team (web dashboard)
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const digits = form.mobile.replace(/\D/g, "").replace(/^91(?=\d{10}$)/, "");
  const problem =
    form.name.trim().length < 2
      ? "Enter the full name."
      : digits.length !== 10
      ? "Enter a 10-digit mobile number."
      : form.age && (Number(form.age) < 16 || Number(form.age) > 100)
      ? "Age must be between 16 and 100."
      : passwordProblem(form.password);

  const save = async () => {
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await addTeamMember({
        name: form.name.trim(),
        mobile: digits,
        email: form.email.trim() || null,
        age: form.age ? Number(form.age) : null,
        gender: form.gender || null,
        password: form.password,
        can_view_history: form.role === "admin" ? false : form.can_view_history,
        role: form.role,
      });
      onAdded(res.user, form.password, res.linked_surveys || 0);
    } catch (err) {
      setError(errorText(err, "The team member couldn't be added."));
      setBusy(false);
    }
  };

  return (
    <Modal
      title={form.role === "admin" ? "Add admin" : "Add team member"}
      subtitle={form.role === "admin"
        ? "Create their login. Admins sign in to this dashboard (and the app) with this mobile number or email and password."
        : "Create their login. They sign in to the mobile app with this mobile number and password."}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>
            {busy ? "Adding…" : "Add member"}
          </button>
        </>
      }
    >
      <div className="field">
        <span className="field-label">Role <span className="req" aria-hidden="true">*</span></span>
        <div className="segmented" role="radiogroup" aria-label="Role">
          {[["executive", "Survey person"], ["admin", "Admin"]].map(([value, label]) => (
            <button key={value} type="button" role="radio" aria-checked={form.role === value}
              className={form.role === value ? "is-on" : ""} onClick={() => setForm({ ...form, role: value })}>
              {label}
            </button>
          ))}
        </div>
        {form.role === "admin" && (
          <span className="muted small">Admins manage the team, assign surveys, review reports and see every response. Add an email so they can reset their password.</span>
        )}
      </div>
      <label className="field">
        <span className="field-label">Full name <span className="req" aria-hidden="true">*</span></span>
        <input className="input" value={form.name} onChange={set("name")} placeholder="e.g. Riya Kapoor" autoComplete="off" />
      </label>
      <div className="catalog-row">
        <label className="field">
          <span className="field-label">Mobile number <span className="req" aria-hidden="true">*</span></span>
          <input className="input" type="tel" inputMode="tel" value={form.mobile} onChange={set("mobile")} placeholder="98765 43210" />
        </label>
        <label className="field">
          <span className="field-label">Email</span>
          <input className="input" type="email" value={form.email} onChange={set("email")} placeholder="Optional" />
        </label>
      </div>
      <div className="catalog-row">
        <label className="field">
          <span className="field-label">Age</span>
          <input className="input" type="number" min="16" max="100" value={form.age} onChange={set("age")} placeholder="Optional" />
        </label>
        <label className="field">
          <span className="field-label">Gender</span>
          <Select className="input" value={form.gender} onChange={set("gender")}>
            <option value="">Choose</option>
            {GENDERS.map((g) => <option key={g} value={g}>{g}</option>)}
          </Select>
        </label>
      </div>
      <PasswordField value={form.password} onChange={(password) => setForm({ ...form, password })} />
      {form.role !== "admin" && <label className="member-check">
        <input type="checkbox" checked={form.can_view_history}
          onChange={(e) => setForm({ ...form, can_view_history: e.target.checked })} />
        <span>
          <strong>Can see their survey history</strong>
          <span className="muted"> Ratings and summaries of their own surveys in the app.</span>
        </span>
      </label>}
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

export function CredentialsDialog({ member, password, linked, onClose, reset = false }) {
  const [copied, setCopied] = useState(false);
  const text =
    `FMCG Field Survey app login\n` +
    `Name: ${member.name}\n` +
    `Mobile: ${member.mobile}\n` +
    `Password: ${password}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <Modal
      title={reset ? "New password set" : `${member.name} is in the team`}
      subtitle="Share these login details with them. The password isn't shown again."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={copy}>{copied ? "Copied ✓" : "Copy details"}</button>
          <button type="button" className="btn btn-primary" onClick={onClose}>Done</button>
        </>
      }
    >
      <dl className="credentials">
        <dt>Mobile number</dt>
        <dd>{member.mobile}</dd>
        <dt>Password</dt>
        <dd className="credentials-password">{password}</dd>
      </dl>
      <p className="muted credentials-note">
        They open the app, choose <strong>Log in</strong>, and enter these details.
        {linked > 0 && ` ${linked} earlier ${linked === 1 ? "survey" : "surveys"} done under this name now count as theirs.`}
      </p>
    </Modal>
  );
}

export function ResetPasswordDialog({ member, onClose, onDone }) {
  const [password, setPassword] = useState(generatePassword());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    const problem = passwordProblem(password);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    try {
      await resetMemberPassword(member.id, password);
      onDone(password);
    } catch (err) {
      setError(errorText(err, "The password couldn't be changed."));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Reset ${member.name}'s password`}
      subtitle="Their old password stops working. Give them the new one."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Set password"}</button>
        </>
      }
    >
      <PasswordField value={password} onChange={setPassword} label="New password *" />
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

/*
  "What can <person> survey?"
  Rule (services/product_access.py): the Super Admin starts each round; an admin
  assigns the LIVE round to survey persons, and only they see the product. Nothing
  is open to everyone, a new round is assigned again, and assigning one person
  never changes what the others can survey.
*/

const firstName = (name) => (name || "").trim().split(/\s+/)[0] || name;

export function AssignProductsDialog({ member, team = [], onClose, onSaved }) {
  const [state, setState] = useState({ loading: true, error: "", products: [] });
  const [selected, setSelected] = useState(() => new Set(member.assigned_products || []));
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const disabled = member.is_active === false;

  useEffect(() => {
    let alive = true;
    Promise.all([listProducts(undefined, false), getMemberProducts(member.id)])
      .then(([p, mine]) => {
        if (!alive) return;
        // Products with a live round, and existing products that stay open without one.
        const products = (Array.isArray(p) ? p : p?.products || []).filter((x) => x.is_active && (x.live_round || x.needs_round === false));
        setState({ loading: false, error: "", products });
        setSelected(new Set(mine?.product_ids || [])); // the server's current truth
      })
      .catch((err) => alive && setState((s) => ({ ...s, loading: false, error: errorText(err, "Products couldn't be loaded.") })));
    return () => {
      alive = false;
    };
  }, [member.id]);

  // Others already assigned to each live round (from the team list).
  const others = (pid) =>
    team.filter((u) => u.role === "executive" && u.id !== member.id && (u.assigned_products || []).includes(pid)).map((u) => firstName(u.name));

  const toggle = (id) =>
    setSelected((cur) => {
      const next = new Set(cur);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const query = q.trim().toLowerCase();
  const groups = {};
  state.products
    .filter((p) => !query || `${p.name} ${p.sku || ""} ${p.category_name || ""}`.toLowerCase().includes(query))
    .forEach((p) => (groups[p.category_name || "Other"] ||= []).push(p));
  const chosen = state.products.filter((p) => selected.has(p.id));

  const save = async () => {
    setBusy(true);
    try {
      const res = await saveMemberProducts(member.id, [...selected]);
      onSaved(res.product_ids || []);
    } catch (err) {
      setState((s) => ({ ...s, error: errorText(err, "The products couldn't be saved.") }));
      setBusy(false);
    }
  };

  const first = firstName(member.name);
  return (
    <Modal
      title={`What can ${member.name} survey?`}
      subtitle={`Tick the products ${first} surveys.`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={busy || state.loading || disabled} onClick={save}>{busy ? "Saving…" : "Save"}</button>
        </>
      }
    >
      {disabled && (
        <p className="form-error" role="alert">{member.name} is disabled. Reactivate them on the Team page to assign products.</p>
      )}
      <input className="input" type="search" placeholder="Search products" value={q} onChange={(e) => setQ(e.target.value)} />
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      {state.loading ? (
        <p className="muted">Loading…</p>
      ) : !state.products.length ? (
        <p className="muted">No product has a live survey. The Super Admin starts surveys; you can assign them here once started.</p>
      ) : (
        <>
          <div className="assign-list">
            {Object.entries(groups).map(([cat, items]) => (
              <fieldset key={cat} className="assign-group" disabled={disabled}>
                <legend>{cat}</legend>
                {items.map((p) => {
                  const also = others(p.id);
                  return (
                    <label key={p.id} className={`assign-item${selected.has(p.id) ? " is-on" : ""}`}>
                      <input type="checkbox" checked={selected.has(p.id)} onChange={() => toggle(p.id)} />
                      <span className="assign-item-text">
                        <strong>{p.name}</strong>
                        <span className="muted" title={p.live_round ? `Survey ${p.live_round.number} (started by the Super Admin)` : "Existing product: open to everyone until you assign people"}>
                          {p.live_round ? `Survey ${p.live_round.number}` : "Existing"}
                        </span>
                      </span>
                      <span className={`audience audience-${also.length ? "other" : selected.has(p.id) ? "mine" : "open"}`} title="Who can survey it">
                        {also.length
                          ? `Also: ${also.join(", ")}`
                          : selected.has(p.id)
                          ? `Only ${first}`
                          : p.live_round
                          ? "Nobody yet"
                          : "Everyone"}
                      </span>
                    </label>
                  );
                })}
              </fieldset>
            ))}
            {!Object.keys(groups).length && <p className="muted">No products match.</p>}
          </div>

          <div className="assign-result" role="status" aria-live="polite">
            <p className="assign-result-main">
              <strong>{first}: {chosen.length} of {state.products.length} {state.products.length === 1 ? "product" : "products"}</strong>
            </p>
          </div>
        </>
      )}
    </Modal>
  );
}


/* Admins (role "admin") on the Team page: the main list shows survey persons only.
   Add an admin with "Add team member" (Role: Admin). Nobody can disable or demote
   their own account, and the last active admin stays (checked by the server too). */
export function AdminsPanel({ users = [], meId, onChanged, onNotice, onCredentials }) {
  const admins = users.filter((u) => u.role === "admin").sort((a, b) => a.name.localeCompare(b.name));
  const [busy, setBusy] = useState(null);
  const run = async (id, work, done) => {
    setBusy(id);
    try {
      await work();
      onNotice?.(done);
      onChanged?.();
    } catch (err) {
      onNotice?.(errorText(err, "That didn't work. Try again."));
    } finally {
      setBusy(null);
    }
  };
  const toggle = async (u) => {
    const ok = await confirmDialog({
      title: u.is_active ? `Disable ${u.name}?` : `Reactivate ${u.name}?`,
      message: u.is_active ? "They're signed out everywhere and can't sign in until reactivated. Their account and activity are kept." : "They can sign in again.",
      confirmLabel: u.is_active ? "Disable" : "Reactivate",
      danger: Boolean(u.is_active),
    });
    if (ok) run(u.id, () => updateTeamMember(u.id, { is_active: !u.is_active }), u.is_active ? `${u.name} is disabled.` : `${u.name} is active again.`);
  };
  const reset = async (u) => {
    const ok = await confirmDialog({ title: `Reset ${u.name}'s password?`, message: "A new password is created and shown once; they're signed out everywhere.", confirmLabel: "Reset password" });
    if (!ok) return;
    const password = generatePassword();
    setBusy(u.id);
    try {
      await resetMemberPassword(u.id, password);
      onCredentials?.(u, password);
      onChanged?.();
    } catch (err) {
      onNotice?.(errorText(err, "The password couldn't be reset."));
    } finally {
      setBusy(null);
    }
  };
  const signOut = async (u) => {
    const ok = await confirmDialog({ title: `Sign ${u.name} out everywhere?`, message: "Every sign-in of this account ends now. Their password stays the same.", confirmLabel: "Sign out everywhere" });
    if (ok) run(u.id, () => signOutMember(u.id), `${u.name} is signed out everywhere.`);
  };
  return (
    <section className="panel admins-panel" aria-labelledby="admins-title">
      <div className="admins-head">
        <h2 id="admins-title" className="report-h">Admins <span className="count-badge">{admins.length}</span></h2>
        <p className="muted">The research team: they manage the team, assign surveys and review reports. Add one with <strong>Add team member</strong> → Role: Admin.</p>
      </div>
      <ul className="admins-list">
        {admins.map((u) => (
          <li key={u.id} className={u.is_active ? "" : "is-off"}>
            <span className="admins-who">
              <strong>{u.name}</strong>{u.id === meId && <span className="status-chip is-neutral">You</span>}
              <span className="muted">{[u.mobile, u.email].filter(Boolean).join(" · ")}</span>
              <span className="muted small">{u.last_login_at ? `Last sign-in ${new Date(u.last_login_at).toLocaleString()}` : "Never signed in"}</span>
            </span>
            <span className={`status-chip ${u.is_active ? "is-ok" : "is-neg"}`}>{u.is_active ? "Active" : "Disabled"}</span>
            <span className="admins-actions">
              <button type="button" className="link-btn" disabled={busy === u.id} onClick={() => reset(u)}>Reset password</button>
              <button type="button" className="link-btn" disabled={busy === u.id} onClick={() => signOut(u)}>Sign out everywhere</button>
              {u.id !== meId && (
                <button type="button" className={`link-btn${u.is_active ? " danger-link" : ""}`} disabled={busy === u.id} onClick={() => toggle(u)}>
                  {u.is_active ? "Disable" : "Reactivate"}
                </button>
              )}
            </span>
          </li>
        ))}
        {!admins.length && <li className="muted">No admins listed.</li>}
      </ul>
    </section>
  );
}

