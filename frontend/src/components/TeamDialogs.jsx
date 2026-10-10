import { useEffect, useState } from "react";
import Modal from "./Modal";
import { addTeamMember, getMemberProducts, resetMemberPassword, saveMemberProducts } from "../services/teamApi";
import { listProducts } from "../services/catalogAdminApi";

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
  if (err?.code === "ERR_NETWORK") return "Can't reach the backend on port 8000.";
  return fallback;
}

const passwordProblem = (p) =>
  p.length < 8 || !/[A-Za-z]/.test(p) || !/\d/.test(p) ? "Use at least 8 characters with letters and numbers." : "";

function PasswordField({ value, onChange, label = "Password *" }) {
  const [show, setShow] = useState(true);
  return (
    <label className="field">
      <span className="field-label">{label}</span>
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
        can_view_history: form.can_view_history,
      });
      onAdded(res.user, form.password, res.linked_surveys || 0);
    } catch (err) {
      setError(errorText(err, "The team member couldn't be added."));
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Add team member"
      subtitle="Create their login. They sign in to the mobile app with this mobile number and password."
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
      <label className="field">
        <span className="field-label">Full name *</span>
        <input className="input" value={form.name} onChange={set("name")} placeholder="e.g. Riya Kapoor" autoComplete="off" />
      </label>
      <div className="catalog-row">
        <label className="field">
          <span className="field-label">Mobile number *</span>
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
          <select className="input" value={form.gender} onChange={set("gender")}>
            <option value="">Choose</option>
            {GENDERS.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        </label>
      </div>
      <PasswordField value={form.password} onChange={(password) => setForm({ ...form, password })} />
      <label className="member-check">
        <input type="checkbox" checked={form.can_view_history}
          onChange={(e) => setForm({ ...form, can_view_history: e.target.checked })} />
        <span>
          <strong>Can see their survey history</strong>
          <span className="muted"> Ratings and summaries of their own surveys in the app.</span>
        </span>
      </label>
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
  Rule: a product given to nobody is shown to EVERYONE. A product given to one or
  more people is shown ONLY to them. The dialog shows each product's current
  audience and, before saving, exactly what changes for this person and the others.
*/

const firstName = (name) => (name || "").trim().split(/\s+/)[0] || name;

// Who can survey each product, from everyone's assignments.
function ownersMap(team, products, member, selected) {
  const map = {};
  products.forEach((p) => (map[p.id] = new Set()));
  team.forEach((u) => {
    if (u.role === "admin" || u.id === member.id) return;
    (u.assigned_products || []).forEach((pid) => map[pid]?.add(u.id));
  });
  if (member && selected) selected.forEach((pid) => map[pid]?.add(member.id));
  return map;
}

const canSee = (owners, userId) => owners.size === 0 || owners.has(userId);

export function AssignProductsDialog({ member, team = [], onClose, onSaved }) {
  const [state, setState] = useState({ loading: true, error: "", products: [] });
  const [selected, setSelected] = useState(() => new Set(member.assigned_products || []));
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([listProducts(undefined, false), getMemberProducts(member.id)])
      .then(([p, mine]) => {
        if (!alive) return;
        const products = (Array.isArray(p) ? p : p?.products || []).filter((x) => x.is_active);
        setState({ loading: false, error: "", products });
        setSelected(new Set(mine?.product_ids || [])); // the server's current truth
      })
      .catch((err) => alive && setState((s) => ({ ...s, loading: false, error: errorText(err, "Products couldn't be loaded.") })));
    return () => {
      alive = false;
    };
  }, [member.id]);

  const people = team.filter((u) => u.role !== "admin");
  const nameOf = (id) => (id === member.id ? firstName(member.name) : firstName(people.find((u) => u.id === id)?.name || "Someone"));
  const before = ownersMap(team, state.products, member, new Set(member.assigned_products || []));
  const after = ownersMap(team, state.products, member, selected);

  const toggle = (id) =>
    setSelected((cur) => {
      const next = new Set(cur);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // What this person will see, and what changes for everyone else.
  const willSee = state.products.filter((p) => canSee(after[p.id], member.id));
  const changes = people
    .filter((u) => u.id !== member.id)
    .map((u) => ({
      user: u,
      lose: state.products.filter((p) => canSee(before[p.id], u.id) && !canSee(after[p.id], u.id)),
      gain: state.products.filter((p) => !canSee(before[p.id], u.id) && canSee(after[p.id], u.id)),
    }))
    .filter((c) => c.lose.length || c.gain.length);

  const audience = (pid) => {
    const owners = after[pid] || new Set();
    if (!owners.size) return { tone: "open", text: "Everyone" };
    const names = [...owners].map(nameOf);
    const mine = owners.has(member.id);
    return { tone: mine ? "mine" : "other", text: mine ? names.join(", ") : `Only ${names.join(", ")}` };
  };

  const query = q.trim().toLowerCase();
  const groups = {};
  state.products
    .filter((p) => !query || `${p.name} ${p.sku || ""} ${p.category_name || ""}`.toLowerCase().includes(query))
    .forEach((p) => (groups[p.category_name || "Other"] ||= []).push(p));

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
      subtitle={`Tick the products you give to ${first}. A product given to someone is shown only to them; products given to nobody are shown to everyone.`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={busy || state.loading} onClick={save}>{busy ? "Saving…" : "Save"}</button>
        </>
      }
    >
      <input className="input" type="search" placeholder="Search products" value={q} onChange={(e) => setQ(e.target.value)} />
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      {state.loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="assign-list">
            {Object.entries(groups).map(([cat, items]) => (
              <fieldset key={cat} className="assign-group">
                <legend>{cat}</legend>
                {items.map((p) => {
                  const a = audience(p.id);
                  return (
                    <label key={p.id} className={`assign-item${selected.has(p.id) ? " is-on" : ""}`}>
                      <input type="checkbox" checked={selected.has(p.id)} onChange={() => toggle(p.id)} />
                      <span className="assign-item-text">
                        <strong>{p.name}</strong>
                        <span className="muted">{p.sku || "No SKU"}</span>
                      </span>
                      <span className={`audience audience-${a.tone}`} title="Who can survey this product">
                        {a.tone === "open" ? "👥 " : "🔒 "}
                        {a.text}
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
              <strong>{first} will see {willSee.length} of {state.products.length} {state.products.length === 1 ? "product" : "products"}</strong>
              {willSee.length > 0 && willSee.length < state.products.length && (
                <span className="muted">: {willSee.map((p) => p.name).join(", ")}</span>
              )}
              {willSee.length === state.products.length && <span className="muted"> (all of them)</span>}
            </p>
            {changes.map((c) => (
              <p key={c.user.id} className="assign-result-other">
                {c.lose.length > 0 && (
                  <span className="lose">⚠ {c.user.name} will no longer see: {c.lose.map((p) => p.name).join(", ")}</span>
                )}
                {c.gain.length > 0 && (
                  <span className="gain">{c.user.name} will now also see: {c.gain.map((p) => p.name).join(", ")}</span>
                )}
              </p>
            ))}
          </div>
        </>
      )}
    </Modal>
  );
}