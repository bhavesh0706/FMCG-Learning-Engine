import { useCallback, useEffect, useMemo, useState } from "react";
import Modal from "./Modal";
import { confirmDialog } from "./ConfirmDialog";
import { lovValues } from "../services/lov";
import { getTeam } from "../services/teamApi";
import {
  allocatePlan, createActionPlan, deleteActionPlan, getActionPlans, orgError, updateActionPlan,
} from "../services/orgApi";
import Select from "./Select";
import DateField, { todayIso } from "./DateField";

/*
  Action plans: several per survey, each with its own team, cities, target and dates.
  The Super Admin makes them in a project's Survey plan (OrgPortal ProjectPage), before the
  survey starts and while it runs. Backend: api/action_plans.py, api/projects.py.
    Super Admin: add / edit / delete plans (project page)
    Admin:       allocates team members and their cities to each plan (ActionPlansSection)
*/

export const emptyPlan = () => ({ target: "", start_date: "", end_date: "", cities: [], audience: "", notes: "" });

export const planForm = (plan) => ({
  ...emptyPlan(),
  ...Object.fromEntries(Object.entries(plan || {}).map(([k, v]) => [k, v ?? (k === "cities" ? [] : "")])),
});

// The API body: numbers as numbers, empty as null
export const planBody = (form) => ({
  target: form.target === "" ? null : Number(form.target),
  persons_needed: null, // no longer asked: the Admin allocates the team
  start_date: form.start_date || null,
  end_date: form.end_date || null,
  cities: form.cities || [],
  audience: form.audience || null,
  notes: form.notes?.trim() || null,
});

// A positive whole number typed as digits only: 1, 5, 100 (never 5.5, -5, +5, 0, 1e2 or letters).
export const WHOLE_NUMBER = /^[1-9][0-9]*$/;
export const wholeNumberProblem = (value, label = "Consumer responses") => {
  const text = String(value ?? "").trim();
  if (!text) return `${label}: enter a number.`;
  if (!WHOLE_NUMBER.test(text)) return `${label} must be a whole number from 1 (1, 2, 3 …): no decimals, signs, letters or zero.`;
  return "";
};

export const planProblem = (form, needTarget = false) => {
  if (needTarget || String(form.target ?? "").trim() !== "") {
    const p = wholeNumberProblem(form.target);
    if (p) return p;
  }
  if (form.start_date && form.end_date && form.end_date < form.start_date) return "The end date is before the start date.";
  return "";
};

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const fmtDate = (d) => (d ? new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "");
const span = (p) => (p.start_date || p.end_date ? `${fmtDate(p.start_date) || "…"} – ${fmtDate(p.end_date) || "…"}` : "No dates set");
const AUDIENCE = { internal: "Internal", external: "External" };

/* Cities as chips; suggestions from the database list CITY (Catalog > Master lists). */
export function CitiesInput({ value = [], onChange, options, placeholder = "Type a city and press Enter (empty = anywhere)", id }) {
  const [text, setText] = useState("");
  const suggestions = useMemo(() => options || [...new Set(lovValues("CITY").map((v) => v.label))].sort(), [options]);
  const add = (raw) => {
    const city = raw.replace(/,/g, " ").trim().replace(/\s+/g, " ");
    if (city && !value.some((c) => c.toLowerCase() === city.toLowerCase())) onChange([...value, city]);
    setText("");
  };
  const listId = `${id || "cities"}-list`;
  return (
    <div className="ap-cities">
      {value.map((c) => (
        <span key={c} className="ap-chip">
          {c}
          <button type="button" aria-label={`Remove ${c}`} onClick={() => onChange(value.filter((x) => x !== c))}>×</button>
        </span>
      ))}
      <input id={id} className="input ap-city-input" list={listId} value={text} placeholder={value.length ? "Add a city" : placeholder}
        onChange={(e) => { const v = e.target.value; if (suggestions.includes(v)) add(v); else setText(v); }}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(text); } }}
        onBlur={() => text.trim() && add(text)} />
      <datalist id={listId}>{suggestions.map((c) => <option key={c} value={c} />)}</datalist>
    </div>
  );
}

/* The plan form: responses, dates, cities, audience, notes. */
export function PlanFields({ value, onChange, idPrefix = "plan" }) {
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value });
  const today = todayIso();
  const targetText = String(value.target ?? "");
  const targetProblem = targetText.trim() ? wholeNumberProblem(targetText) : "";
  return (
    <div className="ap-fields">
      <label className="field">
        <span className="field-label">Consumer responses *</span>
        <input className={`input${targetProblem ? " is-invalid" : ""}`} type="text" inputMode="numeric" pattern="[1-9][0-9]*"
          autoComplete="off" value={targetText} onChange={set("target")} placeholder="e.g. 30" aria-invalid={Boolean(targetProblem)}
          aria-describedby={targetProblem ? `${idPrefix}-target-err` : undefined}
          onKeyDown={(e) => { if (["e", "E", "+", "-", ".", ","].includes(e.key)) e.preventDefault(); }} />
        {targetProblem && <span id={`${idPrefix}-target-err`} className="field-error">{targetProblem}</span>}
      </label>
      <label className="field">
        <span className="field-label">Audience</span>
        <Select className="input" value={value.audience} onChange={set("audience")}>
          <option value="">Internal and external</option>
          <option value="internal">Internal only</option>
          <option value="external">External only</option>
        </Select>
      </label>
      <div className="field">
        <span className="field-label" id={`${idPrefix}-start-l`}>Start date</span>
        <DateField id={`${idPrefix}-start`} ariaLabel="Start date" value={value.start_date} min={today}
          onChange={(d) => onChange({ ...value, start_date: d, end_date: value.end_date && d && value.end_date < d ? "" : value.end_date })} />
      </div>
      <div className="field">
        <span className="field-label" id={`${idPrefix}-end-l`}>End date</span>
        <DateField id={`${idPrefix}-end`} ariaLabel="End date" value={value.end_date}
          min={value.start_date && value.start_date > today ? value.start_date : today}
          onChange={(d) => onChange({ ...value, end_date: d })} />
      </div>
      <div className="field is-wide">
        <span className="field-label">Cities</span>
        <CitiesInput id={`${idPrefix}-cities`} value={value.cities} onChange={(cities) => onChange({ ...value, cities })} />
      </div>
      <label className="field is-wide">
        <span className="field-label">Other requirements</span>
        <textarea className="input" rows={2} value={value.notes} onChange={set("notes")}
          placeholder="e.g. modern trade stores only, chilled samples, mothers of school kids" />
      </label>
    </div>
  );
}

// `create` (optional) saves a new plan: by default into the product's live survey.
// `limit`: the most consumers this plan may have (the product's total minus the other plans).
export function PlanDialog({ productId, plan, onClose, onSaved, create, title, limit = null, total = null }) {
  const [name, setName] = useState(plan?.name || "");
  const [form, setForm] = useState(planForm(plan));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (name.trim().length < 2) return setError("Give the plan a name, e.g. “Delhi team”.");
    const problem = planProblem(form, true);
    if (problem) return setError(problem);
    if (limit != null && Number(form.target) > limit) {
      return setError(`This plan can have at most ${limit} consumers: the project's response target is ${total} and the other plans `
        + `already have ${total - limit}. To plan more, raise the target first (Edit response target).`);
    }
    setBusy(true);
    try {
      const body = { name: name.trim(), ...planBody(form) };
      onSaved(plan ? await updateActionPlan(plan.id, body) : create ? await create(body) : await createActionPlan(productId, body));
    } catch (e) {
      setError(orgError(e, "The action plan couldn't be saved."));
      setBusy(false);
    }
  };
  return (
    <Modal title={plan ? `Edit “${plan.name}”` : title || "Add an action plan"}
      subtitle="Its own target, dates and cities; the Admin then allocates the team." onClose={onClose}
      footer={<>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : plan ? "Save plan" : "Add plan"}</button>
      </>}>
      <label className="field">
        <span className="field-label">Plan name *</span>
        <input className="input" value={name} maxLength={150} onChange={(e) => setName(e.target.value)} placeholder="e.g. Delhi team" />
      </label>
      {limit != null && (
        <p className="field-hint ap-limit">Up to <strong>{limit}</strong> consumer responses for this plan (response target {total}).</p>
      )}
      <PlanFields value={form} onChange={setForm} idPrefix="action-plan" />
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

/* One survey plan as a card: name and state, responses (and progress once collecting), dates,
   cities, audience, team with its allocation state, other requirements, then the actions. */
const initials = (name) => (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

export function PlanCard({ p, canAllocate, canEdit, onTeam, onEdit, onDelete }) {
  const collecting = Boolean(p.round_id);
  const pct = p.target ? Math.min(100, Math.round((100 * p.done) / p.target)) : 0;
  const left = Math.max(0, (p.target || 0) - (p.done || 0));
  const state = !collecting ? { label: "Planned", tone: "is-neutral" } : left === 0 ? { label: "Done", tone: "is-ok" } : { label: "Collecting", tone: "is-live" };
  const staffed = p.members.length > 0;
  const deletable = canEdit && !p.done;
  return (
    <article className="plan-card">
      <header className="plan-card-head">
        <div className="plan-card-title">
          <h3>{p.name}</h3>
          <span className={`plan-state ${state.tone}`}>{state.label}</span>
        </div>
        <div className="plan-card-count">
          <strong>{collecting ? p.done : p.target}</strong>
          <span>{collecting ? `of ${p.target} responses` : `consumer response${p.target === 1 ? "" : "s"}`}</span>
        </div>
      </header>
      {collecting && (
        <div className="plan-progress">
          <div className="ap-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${p.name} progress`}>
            <span style={{ width: `${pct}%` }} />
          </div>
          <span className="muted small">{p.done} submitted · {left} remaining</span>
        </div>
      )}
      <dl className="plan-facts">
        <div><dt>Dates</dt><dd>{span(p)}</dd></div>
        <div><dt>Audience</dt><dd>{p.audience ? `${AUDIENCE[p.audience]} only` : "Internal and external"}</dd></div>
        <div className="is-wide">
          <dt>Cities</dt>
          <dd className="plan-chips">{p.cities.length ? p.cities.map((c) => <span key={c} className="ap-chip is-small">{c}</span>) : <span className="muted">Any city</span>}</dd>
        </div>
      </dl>
      <section className="plan-team" aria-label={`Team of ${p.name}`}>
        <div className="plan-team-head">
          <strong>Team</strong>
          <span className={`status-chip ${staffed ? "is-ok" : "is-warn"}`}>{staffed ? `${plural(p.members.length, "survey person")} allocated` : "Not allocated yet"}</span>
        </div>
        {staffed ? (
          <ul className="plan-people">
            {p.members.map((m) => (
              <li key={m.user_id}>
                <span className="plan-avatar" aria-hidden="true">{initials(m.name)}</span>
                <span className="plan-person"><span>{m.name}</span><span className="muted small">{m.cities.length ? m.cities.join(", ") : "Any city"}</span></span>
              </li>
            ))}
          </ul>
        ) : <p className="muted small">The Admin allocates the survey persons for this plan.</p>}
      </section>
      {p.notes && <p className="plan-notes"><span className="plan-notes-label">Other requirements</span>{p.notes}</p>}
      {p.copied_from_id && <p className="muted small">Copied from the previous survey.</p>}
      {(canAllocate || canEdit) && (
        <footer className="plan-card-actions">
          {canAllocate && <button type="button" className="btn btn-primary btn-sm" onClick={onTeam}>{staffed ? "Change team" : "Allocate team"}</button>}
          {canEdit && <button type="button" className="btn btn-secondary btn-sm" onClick={onEdit}>Edit</button>}
          {canEdit && (
            <button type="button" className="btn btn-secondary btn-sm plan-delete" onClick={onDelete} disabled={!deletable}
              title={deletable ? "" : "Responses have come in for this plan, so it can't be deleted"}>Delete</button>
          )}
        </footer>
      )}
    </article>
  );
}

/* Admin: which survey persons work in this plan, and in which of its cities. */
export function AllocateDialog({ plan, plans, onClose, onSaved }) {
  const [people, setPeople] = useState(null);
  const [chosen, setChosen] = useState(() => Object.fromEntries(plan.members.map((m) => [m.user_id, m.cities])));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // A person may work in several plans of a survey; each survey counts in the plan of the customer's city.
  const elsewhere = useMemo(() => {
    const out = {};
    plans.filter((p) => p.id !== plan.id).forEach((p) => p.members.forEach((m) => { (out[m.user_id] ||= []).push(p.name); }));
    return out;
  }, [plans, plan.id]);
  useEffect(() => {
    getTeam().then((r) => setPeople((r.users || []).filter((u) => u.role === "executive" && u.is_active)))
      .catch((e) => setError(orgError(e, "The team couldn't be loaded.")));
  }, []);
  const toggle = (id) => setChosen((c) => {
    const next = { ...c };
    if (next[id]) delete next[id]; else next[id] = [...(plan.cities || [])];
    return next;
  });
  const toggleCity = (id, city) => setChosen((c) => {
    const have = c[id] || [];
    const next = have.includes(city) ? have.filter((x) => x !== city) : [...have, city];
    return { ...c, [id]: next };
  });
  const save = async () => {
    if (plan.cities?.length && Object.values(chosen).some((cities) => !cities.length)) {
      return setError("Give every chosen person at least one city.");
    }
    setBusy(true);
    try {
      onSaved(await allocatePlan(plan.id, Object.entries(chosen).map(([user_id, cities]) => ({ user_id: Number(user_id), cities }))));
    } catch (e) {
      setError(orgError(e, "The team couldn't be saved."));
      setBusy(false);
    }
  };
  return (
    <Modal title={`Team for “${plan.name}”`}
      subtitle={plan.cities?.length ? `Choose the survey persons and the cities each may survey (${plan.cities.join(", ")}).` : "Choose the survey persons; this plan has no city limit."}
      onClose={onClose}
      footer={<>
        <span className="muted small ap-count">{Object.keys(chosen).length} chosen</span>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy || !people} onClick={save}>{busy ? "Saving…" : "Save team"}</button>
      </>}>
      {!people && !error && <p className="muted">Loading the team…</p>}
      <ul className="ap-people">
        {(people || []).map((u) => {
          const taken = elsewhere[u.id];
          const on = Boolean(chosen[u.id]);
          return (
            <li key={u.id}>
              <label className="ap-person">
                <input type="checkbox" checked={on} onChange={() => toggle(u.id)} />
                <span><strong>{u.name}</strong>{taken && <span className="muted small"> · also in {taken.map((n) => `“${n}”`).join(", ")}</span>}</span>
              </label>
              {on && plan.cities?.length > 0 && (
                <span className="ap-city-toggles" role="group" aria-label={`Cities for ${u.name}`}>
                  {plan.cities.map((c) => (
                    <button key={c} type="button" className={`ap-chip-toggle${chosen[u.id].includes(c) ? " is-on" : ""}`}
                      aria-pressed={chosen[u.id].includes(c)} onClick={() => toggleCity(u.id, c)}>{c}</button>
                  ))}
                </span>
              )}
            </li>
          );
        })}
        {people && !people.length && <li className="muted">No active survey persons. Add them on the Team page.</li>}
      </ul>
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

/*
  The action plans of a product's live (or chosen) round.
  role "client": plans them · role "admin": allocates teams. With no plans, an admin sees
  `fallback` (the simple Who can survey list) as before.
*/
export function ActionPlansSection({ productId, role, fallback = null, readOnly = false }) {
  const [round, setRound] = useState(null);
  const [state, setState] = useState({ loading: true, data: null, error: "" });
  const [dialog, setDialog] = useState(null);
  const load = useCallback(() => {
    getActionPlans(productId, round)
      .then((d) => setState({ loading: false, data: d, error: "" }))
      .catch((e) => setState({ loading: false, data: null, error: orgError(e, "Action plans couldn't be loaded.") }));
  }, [productId, round]);
  useEffect(() => { load(); }, [load]);
  const d = state.data;
  const plans = d?.plans || [];
  const [latest, setLatest] = useState(null);
  useEffect(() => { if (d?.round && (latest == null || d.round.number > latest)) setLatest(d.round.number); }, [d, latest]);
  const remove = async (p) => {
    const ok = await confirmDialog({ title: `Delete “${p.name}”?`, message: "Its team allocation is removed too.", confirmLabel: "Delete", danger: true });
    if (!ok) return;
    try {
      await deleteActionPlan(p.id);
      load();
    } catch (e) {
      setState((s) => ({ ...s, error: orgError(e, "The plan couldn't be deleted.") }));
    }
  };
  if (role === "admin" && !state.loading && !plans.length && fallback) return fallback;
  return (
    <div className="ap-wrap">
      <section className="panel" aria-labelledby="ap-title">
        <div className="ap-head">
          <div>
            <h2 id="ap-title" className="report-h">
              Action plans{d?.round ? ` · Survey ${d.round.number}` : ""}
              {d?.round?.recipe_version ? <span className="status-chip is-neutral">Recipe v{d.round.recipe_version}</span> : null}
            </h2>
            <p className="muted">
              {d?.round
                ? `${plans.length} plan${plans.length === 1 ? "" : "s"} · target ${d.round.target} consumers${d.round.status === "live" ? "" : " · this survey has ended"}`
                : "Start a survey to plan its execution."}
            </p>
          </div>
          <span className="ap-actions">
            {latest > 1 && (
              <Select className="input ap-round" aria-label="Survey" value={round ?? ""} onChange={(e) => setRound(e.target.value ? Number(e.target.value) : null)}>
                <option value="">Current survey</option>
                {Array.from({ length: latest }, (_, i) => latest - i).map((n) => <option key={n} value={n}>Survey {n}</option>)}
              </Select>
            )}
            {d?.can_edit && !readOnly && <button type="button" className="btn btn-primary btn-sm" onClick={() => setDialog({ type: "plan" })}>Add action plan</button>}
          </span>
        </div>
        {state.error && <p className="form-error" role="alert">{state.error}</p>}
        {state.loading ? <p className="muted">Loading…</p> : (
          <div className="ap-grid">
            {plans.map((p) => (
              <PlanCard key={p.id} p={p} canAllocate={!readOnly && d.can_allocate} canEdit={!readOnly && d.can_edit}
                onTeam={() => setDialog({ type: "team", plan: p })} onEdit={() => setDialog({ type: "plan", plan: p })} onDelete={() => remove(p)} />
            ))}
            {d?.round && !plans.length && (
              <p className="muted">No action plans in this survey.{d.can_edit ? " Add one per team, city or timeline." : ""}</p>
            )}
          </div>
        )}
      </section>
      {dialog?.type === "plan" && (
        <PlanDialog productId={productId} plan={dialog.plan} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); load(); }} />
      )}
      {dialog?.type === "team" && (
        <AllocateDialog plan={dialog.plan} plans={plans} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); load(); }} />
      )}
    </div>
  );
}