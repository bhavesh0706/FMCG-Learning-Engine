import SectionIcon from "./SectionIcon";
import { nextPlanCode, PlanCard, PlanDialog, wholeNumberProblem } from "./ActionPlans";
import { ProductDialog } from "./CatalogPage";
import { SaveTemplateDialog, UseTemplateDialog } from "./QuestionTemplates";
import { listCategories } from "../services/catalogAdminApi";
import DateField from "./DateField";
import { OrgPasskeyButton } from "./PasskeyPanels";
import SuperAdminsCard from "./SuperAdminsCard";
import { getRecipe } from "../services/catalogAdminApi";
import { removeStandard } from "../services/nextApi";
import { StandardsCard } from "./RecipeExtras";
import AnalyticsPage from "./AnalyticsPage";
import OrgSurveys from "./OrgSurveys";
import RecipeMaster from "./RecipeMaster";
import ReportDownloads from "./ReportDownloads";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL } from "../config";
import { changePassword, changePasswordSimple, changePasswordWithCode, getSetupStatus, sendMyPasswordCode, signOut, signOutEverywhere } from "../services/auth";
import {
  compareRounds,
  createProject,
  deleteProject,
  endProject,
  endRound,
  deleteRound,
  getProject,
  listProjects,
  projectOptions,
  updateProject,
  getOrgAccount,
  getOrgProduct,
  getOrgProducts,
  getOverview,
  getRoundReport, getRoundSnapshot,
  getUnreadUpdates,
  getUpdates,
  markUpdatesSeen,
  orgError,
  addProjectPlan,
  deleteActionPlan,
  getProjectPlans,
  getProductSummary,
  getSetup,
  setProjectTarget,
  startProjectSurvey,
  standardsFailures,
  recordingAccessHistory,
} from "../services/orgApi";
import { downloadComparisonPdf } from "../services/comparisonPdf";
import CatalogPage, { QuestionsTab, RecipeTab } from "./CatalogPage";
import { ConfirmHost, confirmDialog } from "./ConfirmDialog";
import Modal from "./Modal";
import KpiIcon from "./KpiIcon";
import NavIcon from "./NavIcon";
import Select from "./Select";

/*
  Organization portal: the company whose products are surveyed (users.role = "client").
  Its own login, made on the server (python -m app.tools.organization); the admin
  dashboard has no client options. Backend: app/api/org.py.

  Pages (hash routes, so the browser's Back/Forward work):
    #/overview                          numbers, products, latest updates
    #/products                          every product, filter by round status
    #/products/:id/report[/:round]      the consumer report of one round (+ PDF)
    #/products/:id/recipe               recipe, its versions, compare two versions
    #/products/:id/rounds               every round; start a new one
    #/products/:id/compare              two rounds side by side (+ PDF)
    #/updates                           everything that happened, newest first
    #/account                           organization details, password, sign-ins

  Only aggregated results: no customer names, phone numbers or addresses, no survey
  persons, no recordings and no individual surveys. The research team's admins
  appear as "Research team".
*/

const POLL_MS = 60000;

/* ---------------- helpers ---------------- */

// Server times are UTC; some come without the "Z", so add it before parsing.
const toDate = (iso) => {
  if (!iso) return null;
  const text = String(iso);
  const d = new Date(/T\d{2}:\d{2}/.test(text) && !/(Z|[+-]\d{2}:?\d{2})$/.test(text) ? `${text}Z` : text);
  return Number.isNaN(d.getTime()) ? null : d;
};
const fmtDate = (iso) => toDate(iso)?.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) || "";
const fmtDateTime = (iso) =>
  toDate(iso)?.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) || "";
function ago(iso) {
  const d = toDate(iso);
  if (!d) return "";
  const diff = (d.getTime() - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, secs] of [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]]) {
    if (Math.abs(diff) >= secs) return rtf.format(Math.round(diff / secs), unit);
  }
  return "just now";
}
const one = (v) => (v == null ? "—" : Number(v).toFixed(1));
const plural = (n, word) => `${n} ${n === 1 ? word : `${word}s`}`;
const percent = (done, target) => Math.min(100, Math.round((100 * (done || 0)) / Math.max(1, target || 1)));
const go = (path) => {
  window.location.hash = `#/${path}`;
};
const initialOf = (name) => (name || "?").trim()[0]?.toUpperCase() || "?";

function parseHash() {
  return (window.location.hash || "").replace(/^#\/?/, "").split(/[?]/)[0].split("/").filter(Boolean);
}

function useHashRoute() {
  const [parts, setParts] = useState(parseHash);
  useEffect(() => {
    const onChange = () => {
      setParts(parseHash());
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return parts;
}

// Loads data, reloads every minute while the page is visible, and on demand.
function useData(loader, deps) {
  const [state, setState] = useState({ loading: true, error: "", data: null });
  const ref = useRef(loader);
  useEffect(() => {
    ref.current = loader;
  });
  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setState((s) => ({ ...s, loading: true, error: "" }));
    try {
      const data = await ref.current();
      setState({ loading: false, error: "", data });
    } catch (err) {
      if (silent) return;
      setState({ loading: false, error: orgError(err, "This page couldn't be loaded. Try again in a moment."), data: null });
    }
  }, []);
  // The caller's inputs (product id, round numbers...) as one stable string:
  // when it changes, the data loads again.
  const depsKey = JSON.stringify(deps);
  useEffect(() => {
    load();
    const tick = () => document.visibilityState === "visible" && load({ silent: true });
    const timer = setInterval(tick, POLL_MS);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [load, depsKey]);
  return [state, load];
}

/* ---------------- small pieces ---------------- */

function Icon({ name, size = 16 }) {
  const paths = {
    check: <path d="M20 6 9 17l-5-5" />,
    lock: (
      <>
        <rect x="4" y="11" width="16" height="10" rx="2" />
        <path d="M8 11V7a4 4 0 0 1 8 0v4" />
      </>
    ),
    mail: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="m3 7 9 6 9-6" />
      </>
    ),
    logout: (
      <>
        <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
        <path d="m16 17 5-5-5-5" />
        <path d="M21 12H9" />
      </>
    ),
    edit: (
      <>
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
      </>
    ),
    round: (
      <>
        <path d="M21 12a9 9 0 1 1-3-6.7" />
        <path d="M21 4v5h-5" />
      </>
    ),
    download: (
      <>
        <path d="M12 15V3m0 12-4-4m4 4 4-4" />
        <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
      </>
    ),
    chevron: <path d="m9 18 6-6-6-6" />,
    back: <path d="m15 18-6-6 6-6" />,
  };
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

function Stars({ value }) {
  if (value == null) return null;
  const full = Math.round(value);
  return (
    <span className="org-stars" aria-hidden="true">
      {"★".repeat(full)}
      <i>{"★".repeat(Math.max(0, 5 - full))}</i>
    </span>
  );
}

function RoundChip({ round }) {
  if (!round) return <span className="round-chip is-none">Not started</span>;
  if (round.status === "live") return <span className="round-chip is-live">Survey {round.number} · collecting</span>;
  if (round.closed_reason === "ended") return <span className="round-chip">Survey {round.number} · ended</span>;
  return <span className="round-chip">Survey {round.number} · {round.rating != null ? "report ready" : "completed"}</span>;
}

function Progress({ done, target }) {
  return (
    <div className="org-progress" role="progressbar" aria-valuemin={0} aria-valuemax={target} aria-valuenow={done}
      aria-label={`${done} of ${target} consumers`}>
      <span style={{ width: `${percent(done, target)}%` }} />
    </div>
  );
}

function Loading() {
  return (
    <div className="panel history-loading">
      <span className="spinner" aria-hidden="true" />
      <p>Loading…</p>
    </div>
  );
}

function ErrorPanel({ text, onRetry }) {
  return (
    <div className="panel catalog-empty" role="alert">
      <strong>{text}</strong>
      {onRetry && <button type="button" className="btn btn-secondary" onClick={() => onRetry()}>Try again</button>}
    </div>
  );
}

function Empty({ title, children, action }) {
  return (
    <div className="panel catalog-empty org-empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

function Notice({ notice, onClose }) {
  if (!notice) return null;
  return (
    <p className={`alert ${notice.tone === "error" ? "alert-error" : "alert-ok"} org-notice`} role={notice.tone === "error" ? "alert" : "status"}>
      <span>{notice.text}</span>
      <button type="button" className="link-btn" onClick={onClose}>Dismiss</button>
    </p>
  );
}

const UPDATE_ICON = { recipe: "edit", round_started: "round", round_completed: "check" };

function updateLink(u) {
  if (u.tab === "report" && u.round) return `products/${u.product_id}/report/${u.round}`;
  return `products/${u.product_id}/${u.tab || "report"}`;
}

const UPDATE_KIND_LABEL = { recipe: "Recipe change", round_started: "Survey started", round_completed: "Report" };

// "Today", "Yesterday", or the date: the day heading an update is listed under.
function dayLabel(iso) {
  const d = toDate(iso);
  if (!d) return "Earlier";
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((start(new Date()) - start(d)) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: days < 7 ? "long" : undefined, day: "numeric", month: "short", year: days > 300 ? "numeric" : undefined });
}

/* The feed as a timeline: a coloured marker per kind on a line, the product as a tag,
   "New" on what came in since the last visit. `grouped`: under day headings. */
function UpdatesList({ items, grouped = false, compact = false }) {
  if (!items.length) {
    return (
      <div className="tl-empty">
        <span className="tl-empty-icon"><Icon name="round" size={22} /></span>
        <strong>Nothing yet</strong>
        <span className="muted">Updates appear here as surveys run and recipes change.</span>
      </div>
    );
  }
  const groups = [];
  for (const u of items) {
    const label = grouped ? dayLabel(u.at) : "";
    if (!groups.length || groups[groups.length - 1].label !== label) groups.push({ label, items: [] });
    groups[groups.length - 1].items.push(u);
  }
  return (
    <div className={`tl${compact ? " is-compact" : ""}`}>
      {groups.map((g, gi) => (
        <section key={`${g.label}-${gi}`} className="tl-group" aria-label={g.label || "Updates"}>
          {g.label && <h3 className="tl-day">{g.label}</h3>}
          <ol className="tl-list">
            {g.items.map((u, i) => (
              <li key={`${u.kind}-${u.product_id}-${u.at}-${i}`} className={`tl-item kind-${u.kind}${u.unread ? " is-unread" : ""}`}>
                <span className="tl-marker" aria-hidden="true"><Icon name={UPDATE_ICON[u.kind] || "check"} size={compact ? 15 : 17} /></span>
                <button type="button" className="tl-card" onClick={() => go(updateLink(u))}>
                  <span className="tl-top">
                    <span className="tl-kind">{UPDATE_KIND_LABEL[u.kind] || "Update"}</span>
                    {u.unread && <span className="tl-new">New</span>}
                    <span className="tl-time">{ago(u.at)}</span>
                  </span>
                  <strong className="tl-title">{u.title}</strong>
                  {u.text && <span className="tl-text">{u.text}</span>}
                  <span className="tl-foot">
                    {u.product_name && <span className="tl-product">{u.product_name}</span>}
                    <span className="tl-open">Open <Icon name="chevron" size={14} /></span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}

/* ================================================================
   Shell: top bar, menu, routing
   ================================================================ */

export default function OrgPortal({ user, ReportView }) {
  if (user?.must_change_password) return <SetPasswordScreen user={user} />;
  return <PortalShell user={user} ReportView={ReportView} />;
}

function PortalShell({ user, ReportView }) {
  const parts = useHashRoute();
  const [unread, setUnread] = useState(0);
  const page = parts[0] || "overview";

  const refreshUnread = useCallback(() => {
    getUnreadUpdates()
      .then((d) => setUnread(d?.unread || 0))
      .catch(() => {});
  }, []);
  useEffect(() => {
    refreshUnread();
    const timer = setInterval(() => document.visibilityState === "visible" && refreshUnread(), POLL_MS);
    return () => clearInterval(timer);
  }, [refreshUnread, page]);

  useEffect(() => {
    if (!window.location.hash || !["overview", "products", "catalog", "projects", "recipes", "surveys", "analytics", "updates", "account"].includes(parts[0])) {
      window.history.replaceState(null, "", "#/overview");
    }
    document.title = `${user?.name || "Super Admin"} · Consumer insights`;
  }, [parts, user]);

  let body;
  if (page === "products" && parts[1] && !parts[2]) {
    body = <ProductLanding key={parts[1]} productId={Number(parts[1])} />;
  } else if (page === "products" && parts[1]) {
    body = <ProductPage key={parts[1]} productId={Number(parts[1])} tab={parts[2]} round={parts[3] ? Number(parts[3]) : null} ReportView={ReportView} />;
  } else if (page === "products") body = <ProductsPage />;
  // Projects: one product each, showing its latest catalog setup; surveys start here
  else if (page === "projects" && parts[1] === "new") {
    body = <AddProjectPage key={parts.slice(2).join("/")} prefill={parts[2] ? { productId: Number(parts[2]), version: parts[3] ? Number(parts[3]) : null } : null} />;
  }
  else if (page === "projects" && parts[1] && parts[2] === "edit") body = <EditProjectPage key={parts[1]} projectId={Number(parts[1])} />;
  else if (page === "projects" && parts[1]) {
    body = <ProjectPage key={parts[1]} projectId={Number(parts[1])} />;
  } else if (page === "projects") body = <ProjectsPage />;
  // Products and categories: the same Catalog page as the admin dashboard, with the
  // organization's add / change / deactivate / delete (each change notifies the admins).
  else if (page === "catalog") body = <CatalogPage mode="organization" />;
  // Next update: Recipe Master (costing, standards) and every survey with its report review
  else if (page === "recipes") body = <RecipeMaster onOpenProduct={(id) => go(`products/${id}/standards`)} />;
  else if (page === "surveys") body = <OrgSurveys />;
  else if (page === "analytics") body = <AnalyticsPage />;
  else if (page === "updates") body = <UpdatesPage onSeen={() => setUnread(0)} />;
  else if (page === "account") body = <AccountPage />;
  else body = <OverviewPage user={user} />;

  useEffect(() => {
    document.querySelector(".org-topbar .topnav button.is-active")?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [page]);
  const nav = [
    ["overview", "Overview", "Home"],
    ["catalog", "Categories"],
    ["products", "Products"],
    ["projects", "Projects"],
    ["recipes", "Recipes"],
    ["surveys", "Surveys"],
    ["analytics", "Analytics", "Reports"],
    ["updates", "Updates"],
  ];
  return (
    <div className="app org-app">
      <ConfirmHost />
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="topbar org-topbar">
        <div className="topbar-inner">
          <button type="button" className="brand" onClick={() => go("overview")} aria-label={`${user?.name}, go to the overview`}>
            <span className="org-mark" aria-hidden="true">{initialOf(user?.name)}</span>
            <span className="brand-name">{user?.name}</span>
            <span className="org-brand-sub">Consumer insights</span>
          </button>
          <div className="topbar-right">
            <nav className="topnav" aria-label="Main">
              {nav.map(([key, label, short]) => (
                <button key={key} type="button" className={page === key ? "is-active" : ""} aria-current={page === key ? "page" : undefined}
                  onClick={() => go(key)}>
                  <NavIcon name={key} />
                  {short ? (
                    <>
                      <span className="nav-long">{label}</span>
                      <span className="nav-short" aria-hidden="true">{short}</span>
                    </>
                  ) : label}
                  {key === "updates" && unread > 0 && <span className="nav-count" aria-label={`${unread} new`}>{unread > 99 ? "99+" : unread}</span>}
                </button>
              ))}
            </nav>
            <OrgMenu user={user} />
          </div>
        </div>
      </header>
      <main className="container" id="main" tabIndex={-1}>{body}</main>
    </div>
  );
}

function OrgMenu({ user }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false);
    const esc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const initial = initialOf(user?.name);
  return (
    <div className="user-menu" ref={ref}>
      <button type="button" className="user-chip" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        aria-label={`${user?.name}, Super Admin. Account menu`}>
        <span className="user-avatar" aria-hidden="true">{initial}</span>
        <span className="user-chip-text">
          <strong>{user?.name}</strong>
          <span>Super Admin</span>
        </span>
      </button>
      {open && (
        <div className="user-pop" role="menu">
          <div className="user-pop-head">
            <span className="user-avatar user-avatar-lg" aria-hidden="true">{initial}</span>
            <span>
              <strong>{user?.name}</strong>
              <span>{user?.email || user?.mobile}</span>
              <span className="status-chip is-act user-role">Super Admin</span>
            </span>
          </div>
          <button type="button" role="menuitem" className="user-pop-item" onClick={() => { setOpen(false); go("account"); }}>
            Account
          </button>
          <button type="button" role="menuitem" className="user-pop-item" onClick={() => signOut("")}>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

/* ================================================================
   First sign-in: choose your own password
   ================================================================ */

const passwordProblem = (p) =>
  p.length < 8 || !/[A-Za-z]/.test(p) || !/\d/.test(p) ? "Use at least 8 characters with letters and numbers." : "";

function SetPasswordScreen({ user }) {
  const [form, setForm] = useState({ current: "", next: "", repeat: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const problem = !form.current
    ? "Enter the starting password you signed in with."
    : passwordProblem(form.next) || (form.next !== form.repeat ? "The two new passwords are different." : "");

  const submit = async (e) => {
    e.preventDefault();
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await changePassword(API_BASE_URL, form.current, form.next);
      // The saved session now has must_change_password = false: the portal opens.
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <div className="org-auth">
      <form className="panel org-auth-card" onSubmit={submit} noValidate>
        <div className="org-auth-brand">
          <span className="org-mark is-brand" aria-hidden="true">{initialOf(user?.name)}</span>
          <div>
            <strong>{user?.name}</strong>
            <span className="muted">Consumer insights</span>
          </div>
        </div>
        <p className="eyebrow">First sign-in</p>
        <h1 className="org-auth-title">Choose your own password</h1>
        <p className="muted org-auth-text">
          The starting password from your ione account manager works only once. After this, only your organization knows the password.
        </p>
        <label className="field">
          <span className="field-label">Starting password</span>
          <input className="input" type="password" autoComplete="current-password" value={form.current} onChange={set("current")} />
        </label>
        <label className="field">
          <span className="field-label">New password</span>
          <input className="input" type="password" autoComplete="new-password" placeholder="8+ letters and numbers" value={form.next} onChange={set("next")} />
        </label>
        <label className="field">
          <span className="field-label">Repeat new password</span>
          <input className="input" type="password" autoComplete="new-password" value={form.repeat} onChange={set("repeat")} />
        </label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="btn btn-primary org-auth-submit" disabled={busy}>{busy ? "Saving…" : "Save and continue"}</button>
        <button type="button" className="link-btn org-auth-alt" onClick={() => signOut("")}>Sign out</button>
      </form>
    </div>
  );
}

/* ================================================================
   Overview
   ================================================================ */

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function RatingCell({ product }) {
  const r = product.current_round;
  if (!r || r.rating == null) return <span className="muted">—</span>;
  if (product.previous_rating == null) return <strong>{one(r.rating)}</strong>;
  const up = r.rating >= product.previous_rating;
  const prev = product.previous_round;
  const label = (n, v) => `Survey ${n}${v ? ` (v${v})` : ""}`;
  // The survey just before the current one -> the current survey.
  const caption = prev ? `${label(prev.number, prev.recipe_version)} → ${label(r.number, r.recipe_version)}` : "";
  return (
    <span className="org-rating-change" title={caption}>
      {one(product.previous_rating)} → <strong className={up ? "delta-up" : "delta-down"}>{one(r.rating)}</strong>
      {caption && <span className="rating-caption">{caption}</span>}
    </span>
  );
}

/* The recipe behind a recipe code (RCP-####): the product's latest saved recipe, in a big popup. */
const kgText = (i) => [i.quantity, i.unit].filter((x) => x != null && String(x).trim() !== "").join(" ") || "—";
const rupees = (v) => (v == null ? "—" : `₹${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);

function RecipeCodeDialog({ product, onClose }) {
  const [state, setState] = useState({ loading: true, data: null, error: "" });
  useEffect(() => {
    let alive = true;
    getRecipe(product.id)
      .then((d) => alive && setState({ loading: false, data: d, error: "" }))
      .catch((e) => alive && setState({ loading: false, data: null, error: orgError(e, "The recipe couldn't be loaded.") }));
    return () => {
      alive = false;
    };
  }, [product.id]);
  const d = state.data;
  const recipe = d?.recipe || {};
  const rows = (recipe.ingredients || []).filter((i) => (i.name || "").trim());
  const costs = new Map((d?.costing?.ingredients || []).map((c) => [String(c.name || "").trim().toLowerCase(), c]));
  const code = d?.recipe_code || product.recipe?.code;
  return (
    <Modal size="wide" onClose={onClose}
      title={`${code || "Recipe"} · ${product.name}`}
      subtitle={d ? `Recipe v${d.latest_version ?? product.recipe?.version ?? "—"}${recipe.batch_size ? ` · batch ${recipe.batch_size}` : ""}`
        + `${product.recipe?.by_name ? ` · saved by ${product.recipe.by_name}` : ""}${product.recipe?.at ? ` · ${fmtDate(product.recipe.at)}` : ""}` : null}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Close</button>
          <button type="button" className="btn btn-primary" onClick={() => { onClose(); go(`products/${product.id}/recipe`); }}>Open recipe</button>
        </>
      }
    >
      {state.loading && <Loading />}
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      {d && !rows.length && <p className="muted">This product has no recipe yet.</p>}
      {d && rows.length > 0 && (
        <div className="table-wrap recipe-code-table">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Ingredient</th>
                <th scope="col" className="num-cell">Quantity</th>
                <th scope="col" className="num-cell">Share of batch</th>
                <th scope="col" className="num-cell">Cost per KG</th>
                <th scope="col" className="num-cell">Cost in Use</th>
                <th scope="col">Purpose</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((i, n) => {
                const c = costs.get(String(i.name).trim().toLowerCase());
                return (
                  <tr key={`${i.name}-${n}`}>
                    <td><strong>{i.name}</strong></td>
                    <td className="num-cell">{kgText(i)}</td>
                    <td className="num-cell">{c?.usage_pct != null ? `${+c.usage_pct.toFixed(2)}%` : "—"}</td>
                    <td className="num-cell">{rupees(c?.cost_per_kg)}</td>
                    <td className="num-cell">{rupees(c?.cost_in_use)}</td>
                    <td className="muted">{i.notes || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr>
                <td><strong>Total</strong></td>
                <td className="num-cell"><strong>{d.costing?.batch_kg != null ? `${d.costing.batch_kg} kg` : ""}</strong></td>
                <td className="num-cell"><strong>100%</strong></td>
                <td className="num-cell muted">{d.costing?.batch_cost != null ? `Batch: ${rupees(d.costing.batch_cost)}` : ""}</td>
                <td className="num-cell"><strong>{rupees(d.costing?.cost_per_kg)}</strong>{d.costing?.cost_per_kg != null && <span className="muted"> /kg</span>}</td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      {d && recipe.notes && <p className="recipe-code-notes"><strong>Notes:</strong> {recipe.notes}</p>}
    </Modal>
  );
}

/* ---------------- Setup workflow (api/setup.py, api/projects.py _project_checks) ---------------- */

// Where a setup step is completed (actions come from the server)
const setupPath = (a) => {
  if (!a) return null;
  if (a.kind === "product_tab") return `products/${a.product_id}/${a.tab}`;
  if (a.kind === "add_project") return `projects/new/${a.product_id}${a.version ? `/${a.version}` : ""}`;
  if (a.kind === "project") return `projects/${a.project_id}`;
  if (a.kind === "edit_project") return `projects/${a.project_id}/edit`;
  if (a.kind === "categories") return "catalog";
  return null;
};
const SETUP_ACTION = {
  recipe: "Complete the recipe", standards: "Set the recipe standards", project: "Create the project", category: "Open Categories",
  product: "Open Categories", questions: "Set the survey questions", target: "Set the response target", plan: "Add a survey plan",
  plans_cover: "Complete the survey plan", allocation: "Open the project", start: "Open the project to start",
};

function SetupSteps({ steps, here = null }) {
  return (
    <ol className="wf-steps">
      {steps.map((s) => (
        <li key={s.key} className={`wf-step ${s.done ? "is-done" : "is-todo"}${s.optional ? " is-optional" : ""}`}>
          <span className="wf-dot" aria-hidden="true">{s.done ? "✓" : ""}</span>
          <div>
            <strong>{s.label}</strong>{s.optional && <span className="muted small"> (optional)</span>}
            <span className="visually-hidden">{s.done ? " done" : " pending"}</span>
            <p className="muted small">{s.detail}</p>
          </div>
          {!s.done && setupPath(s.action) && setupPath(s.action) !== here && (
            <button type="button" className="link-btn" onClick={() => go(setupPath(s.action))}>{SETUP_ACTION[s.key] || "Complete"}</button>
          )}
        </li>
      ))}
    </ol>
  );
}

/* Dashboard: every product / project whose setup isn't finished, resumed from its next step */
function SetupSection() {
  const [state, reload] = useData(getSetup, []);
  const [open, setOpen] = useState(null);
  const items = state.data?.items || [];
  if (state.loading && !state.data) return null;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  if (!items.length) return null;
  return (
    <section className="panel org-panel wf-panel" aria-labelledby="setup-title">
      <div className="org-panel-head">
        <div>
          <h2 id="setup-title">Complete your setup</h2>
          <p className="muted small org-panel-sub">
            {items.length} product{items.length === 1 ? "" : "s"} or project{items.length === 1 ? "" : "s"} still being set up · continue where you stopped
          </p>
        </div>
      </div>
      <ul className="wf-list">
        {items.map((it) => {
          const pct = it.total ? Math.round((100 * it.done) / it.total) : 0;
          return (
            <li key={it.key} className="wf-item">
              <div className="wf-item-head">
                <div>
                  <strong>{it.product.name}</strong>
                  <span className="muted small"> · {it.category.name} · recipe v{it.version}{it.project ? ` · ${it.project.code} ${it.project.name}` : ""}</span>
                </div>
                <span className={`status-chip ${it.status === "Ready to start" ? "is-ok" : "is-warn"}`}>{it.status}</span>
              </div>
              <div className="wf-progress" role="progressbar" aria-valuemin={0} aria-valuemax={it.total} aria-valuenow={it.done}
                aria-label={`${it.done} of ${it.total} setup steps done`}>
                <span style={{ width: `${pct}%` }} />
              </div>
              <div className="wf-next">
                <span className="muted small">{it.done} of {it.total} steps done{it.next ? ` · next: ${it.next.label}. ${it.next.detail}` : ""}</span>
                <span className="wf-next-actions">
                  <button type="button" className="link-btn" onClick={() => setOpen(open === it.key ? null : it.key)} aria-expanded={open === it.key}>
                    {open === it.key ? "Hide steps" : "All steps"}
                  </button>
                  {it.next && setupPath(it.next.action) && (
                    <button type="button" className="btn btn-primary btn-sm" onClick={() => go(setupPath(it.next.action))}>
                      {SETUP_ACTION[it.next.key] || "Continue"}
                    </button>
                  )}
                </span>
              </div>
              {open === it.key && <SetupSteps steps={it.steps} />}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* Product workspace: its setup steps, and Done -> Project creation (category and product preselected) */
function ProductSetupBar({ product, refreshKey }) {
  const [item, setItem] = useState(null);
  useEffect(() => {
    let alive = true;
    getSetup().then((d) => {
      if (!alive) return;
      const mine = (d?.items || []).filter((i) => i.product.id === product.id);
      setItem(mine.find((i) => !i.project) || mine[0] || null);
    }).catch(() => alive && setItem(null));
    return () => { alive = false; };
  }, [product.id, refreshKey]);
  if (!item) return null;
  if (item.project) {
    return (
      <div className="org-callout wf-bar">
        <span>Project <strong>{item.project.code}</strong> of recipe v{item.version}: {item.status.toLowerCase()} ({item.done} of {item.total} steps done).</span>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => go(`projects/${item.project.id}`)}>Open project</button>
      </div>
    );
  }
  const required = item.steps.filter((s) => s.key !== "project" && !s.optional);
  const ready = required.every((s) => s.done);
  return (
    <section className="panel wf-bar-panel" aria-label="Product setup">
      <div className="wf-bar-head">
        <div>
          <strong>Product setup</strong>
          <span className="muted small"> · {item.done} of {item.total} steps done</span>
        </div>
        <button type="button" className="btn btn-primary btn-sm" disabled={!ready}
          title={ready ? undefined : "Save the recipe and meet its recipe standards first."}
          onClick={() => go(`projects/new/${product.id}/${item.version}`)}>Done · Continue to project creation</button>
      </div>
      <ol className="wf-stepper">
        {item.steps.map((s) => (
          <li key={s.key} className={s.done ? "is-done" : "is-todo"} title={s.detail}>
            <span className="wf-dot" aria-hidden="true">{s.done ? "✓" : ""}</span>{s.label}
          </li>
        ))}
      </ol>
    </section>
  );
}

function OverviewPage() {
  const [recipeOf, setRecipeOf] = useState(null); // the product whose recipe popup is open
  const [state, reload] = useData(getOverview, []);
  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  const { numbers, products: allRecent, updates } = state.data;
  const products = (allRecent || []).slice(0, 5); // the 5 newest products (the numbers above count all)
  return (
    <section aria-labelledby="org-overview-title">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="org-overview-title">{greeting()}</h1>
          <p className="home-sub">Here's where your products stand with consumers.</p>
        </div>
      </header>

      <div className="home-stats org-kpis">
        <div className="h-stat kpi-card tone-brand">
          <KpiIcon name="products" />
          <span className="h-stat-label">Products</span>
          <span className="h-stat-value">{numbers.products}</span>
          <span className="h-stat-note">{numbers.surveyed} surveyed so far</span>
        </div>
        <div className="h-stat kpi-card tone-teal">
          <KpiIcon name="responses" />
          <span className="h-stat-label">Consumer responses</span>
          <span className="h-stat-value">{numbers.responses}</span>
          <span className="h-stat-note">across all surveys</span>
        </div>
        <div className={`h-stat kpi-card ${numbers.open_rounds ? "tone-pos is-live" : "tone-neutral"}`}>
          <KpiIcon name="live" />
          <span className="h-stat-label">Open surveys</span>
          <span className="h-stat-value">{numbers.open_rounds}</span>
          <span className="h-stat-note">{numbers.open_rounds ? "collecting responses now" : "none collecting right now"}</span>
        </div>
      </div>

      <div className="org-two">
        {/* Left column: setup still to finish (same width as the products table), then the products */}
        <div className="org-left">
          <SetupSection />
        <section className="panel org-panel" aria-labelledby="org-products-title">
          <div className="org-panel-head">
            <div>
              <h2 id="org-products-title">Recently added products</h2>
              <p className="muted small org-panel-sub">The {products.length || 5} newest, latest first</p>
            </div>
            <button type="button" className="link-btn" onClick={() => go("products")}>All products <Icon name="chevron" /></button>
          </div>
          {products.length === 0 ? (
            <p className="muted org-panel-empty">No products yet. Your ione account manager adds them.</p>
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Product</th>
                    <th scope="col">Recipe code</th>
                    <th scope="col">Current survey</th>
                    <th scope="col" className="col-category">Progress</th>
                    <th scope="col">Rating</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((p) => (
                    <tr key={p.id} className="is-clickable" onClick={() => go(`products/${p.id}`)}>
                      <td>
                        <div className="cell-product">
                          <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); go(`products/${p.id}`); }}>{p.name}</button>
                          <span>{p.category_name}{p.created_at ? ` · added ${ago(p.created_at)}` : ""}</span>
                        </div>
                      </td>
                      <td>
                        {p.recipe?.code ? (
                          <button type="button" className="link-btn recipe-code" onClick={(e) => { e.stopPropagation(); setRecipeOf(p); }}
                            aria-label={`Open recipe ${p.recipe.code} of ${p.name}`}>{p.recipe.code}</button>
                        ) : <span className="muted">No recipe yet</span>}
                      </td>
                      <td><RoundChip round={p.current_round} /></td>
                      <td className="col-category">
                        {p.current_round ? (
                          <div className="org-progress-cell">
                            <Progress done={p.current_round.done} target={p.current_round.target} />
                            <span className="muted small">{p.current_round.done} of {p.current_round.target}</span>
                          </div>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td><RatingCell product={p} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        </div>
        <section className="panel org-panel org-updates-panel" aria-labelledby="org-updates-title">
          <div className="org-panel-head">
            <div>
              <h2 id="org-updates-title">Latest updates</h2>
              <p className="muted small org-panel-sub">Recipes, surveys and reports</p>
            </div>
            <button type="button" className="link-btn" onClick={() => go("updates")}>See all <Icon name="chevron" /></button>
          </div>
          {/* Fills the height of the products table beside it; scrolls if there are more. */}
          <div className="org-updates-scroll">
            <UpdatesList items={updates} compact />
          </div>
        </section>
      </div>
      {recipeOf && <RecipeCodeDialog product={recipeOf} onClose={() => setRecipeOf(null)} />}
    </section>
  );
}

/* ================================================================
   Products
   ================================================================ */

const FILTERS = [
  ["all", "All", () => true],
  ["live", "Collecting", (p) => p.current_round?.status === "live"],
  ["done", "Report ready", (p) => p.current_round && p.current_round.status !== "live"],
  ["none", "Not started", (p) => !p.current_round],
];

function ProductsPage() {
  const [state, reload] = useData(getOrgProducts, []);
  // Add product: choose any active category in the dialog (null = closed, else the categories)
  const [adding, setAdding] = useState(null);
  const openAdd = () => listCategories(false).then((d) => setAdding((d?.categories || d || []).filter((c) => c.is_active !== false))).catch(() => setAdding([]));
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const products = useMemo(() => state.data?.products || [], [state.data]);
  const visible = useMemo(() => {
    const test = FILTERS.find((f) => f[0] === filter)[2];
    const query = q.trim().toLowerCase();
    return products.filter(test).filter((p) => !query || `${p.name} ${p.category_name || ""} ${p.sku || ""}`.toLowerCase().includes(query));
  }, [products, filter, q]);

  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  return (
    <section aria-labelledby="org-products-page">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="org-products-page">Products</h1>
          <p className="home-sub">{plural(products.length, "product")}. Open one to read its report, change its recipe or start a new survey.</p>
        </div>
        <span className="head-actions">
          <OrgPasskeyButton />
          <button type="button" className="btn btn-primary" onClick={openAdd}>Add product</button>
        </span>
      </header>
      {adding && (
        <ProductDialog categories={adding} onClose={() => setAdding(null)} onNeedCategory={() => go("catalog")}
          onCreated={(p) => { setAdding(null); go(`products/${p.id}/standards`); }} />
      )}
      <div className="org-toolbar">
        <div className="segmented" role="group" aria-label="Show">
          {FILTERS.map(([key, label, test]) => (
            <button key={key} type="button" aria-pressed={filter === key} className={filter === key ? "is-on" : ""} onClick={() => setFilter(key)}>
              {label} <span className="seg-count">{products.filter(test).length}</span>
            </button>
          ))}
        </div>
        <input className="input org-search" type="search" placeholder="Search products" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search products" />
      </div>
      {visible.length === 0 ? (
        <Empty title="No products match">Choose another filter or clear the search.</Empty>
      ) : (
        <div className="org-cards">
          {visible.map((p) => {
            const r = p.current_round;
            return (
              <button key={p.id} type="button" className="panel org-card" onClick={() => go(`products/${p.id}`)}>
                <span className="eyebrow">{p.category_name || "Product"}</span>
                <strong className="org-card-name">{p.name}</strong>
                <RoundChip round={r} />
                {r ? (
                  <>
                    <Progress done={r.done} target={r.target} />
                    <span className="muted small">{r.done} of {r.target} consumers · Recipe v{r.recipe_version ?? "—"}</span>
                  </>
                ) : (
                  <span className="muted small">Waiting for the first survey</span>
                )}
                <span className="muted small">
                  {r?.rating != null ? (
                    <>Rating <strong className="org-ink">{one(r.rating)}</strong> <Stars value={r.rating} /></>
                  ) : p.recipe ? (
                    `Recipe v${p.recipe.version} ready`
                  ) : (
                    "No recipe yet"
                  )}
                </span>
                <span className="org-card-open">Open <Icon name="chevron" /></span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

/* ================================================================
   One product: Consumer report · Recipe · Survey questions · Rounds · Compare rounds
   ================================================================ */

// Survey questions are edited in the project (one shared set per product): no tab here.
// The old #/products/{id}/questions address still opens them (links in older notifications).
const TABS = [
  ["standards", "Recipe standards"],
  ["recipe", "Recipe"],
  ["report", "Consumer report"],
  ["rounds", "Survey runs"],
  ["recording", "Recording log"],
  ["compare", "Compare surveys"],
];

const SNAPSHOT_TABS = ["recipe", "standards", "questions"];

// The survey box (as on the Consumer report): which survey, its recipe version and responses.
function SurveyBox({ survey, rounds, latest }) {
  if (!survey && !rounds.length) return null; // never surveyed: nothing to choose between
  if (!survey) {
    const users = rounds.filter((r) => r.recipe_version === latest);
    return (
      <div className="report-bar">
        <div>
          <strong>Current setup · Recipe v{latest || 1}</strong>
          <span>
            {users.length ? `Used by ${users.map((r) => `Survey ${r.number}`).join(", ")}. ` : "Not surveyed yet. "}
            Changes here apply to the next survey.
          </span>
        </div>
      </div>
    );
  }
  return (
    <div className="report-bar">
      <div>
        <strong>
          Survey {survey.number}
          <span className={`status-chip ${survey.status === "live" ? "is-ok" : "is-neutral"} report-updating-chip`}>{survey.status === "live" ? "Collecting" : "Completed"}</span>
        </strong>
        <span>
          Recipe v{survey.recipe_version ?? "—"} · {survey.done} of {survey.target} consumers
          {survey.started_at ? ` · started ${fmtDate(survey.started_at)}` : ""}{survey.closed_at ? ` · ended ${fmtDate(survey.closed_at)}` : ""}.
          {" "}This is what Survey {survey.number} used (read-only).
        </span>
      </div>
    </div>
  );
}

const noNotice = () => {};

// Read-only: the recipe, standards or questions one survey used.
function SurveySnapshot({ product, survey, tab }) {
  const [state, reload] = useData(() => getRoundSnapshot(product.id, survey.number), [product.id, survey.number]);
  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  const { recipe, standards } = state.data;
  // Recipe and Survey questions: the same view as the current version, without any edit option.
  if (tab === "recipe") {
    return (
      <div className="org-recipe">
        <section className="panel product-panel">
          <RecipeTab key={`snap-${survey.number}`} product={product} snapshot={state.data} onNotice={noNotice} />
        </section>
      </div>
    );
  }
  if (tab === "questions") {
    return (
      <div className="org-questions">
        <section className="panel product-panel">
          <QuestionsTab key={`snapq-${survey.number}`} product={product} snapshot={state.data.questions} onNotice={noNotice} />
        </section>
      </div>
    );
  }
  if (tab === "standards") {
    const results = standards.results || [];
    return (
      <section className="panel product-panel snap-panel" aria-label={`Recipe standards for Survey ${survey.number}`}>
        <header className="snap-head">
          <h2 className="report-h">Recipe standards · Recipe v{recipe.version ?? "—"}</h2>
          <span className={standards.failed_count ? "po-warn" : "po-ok"}>
            {results.length ? (standards.failed_count ? `${standards.failed_count} of ${results.length} not met` : `${plural(results.length, "standard")}, all met`) : "No standards apply"}
          </span>
        </header>
        <p className="muted small">The standards in place today, checked against the recipe this survey used.</p>
        <ul className="std-results">
          {results.map((r) => (
            <li key={r.rule_id}>
              <span><strong>{r.name}</strong><span className="muted"> · expected {r.expected}{r.actual != null ? ` · actual ${r.actual}` : ""}</span></span>
              <span className={`status-chip ${r.passed ? "is-ok" : "is-warn"}`}>{r.passed ? "Met" : "Not met"}</span>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  return null;
}

/* A product opened without a tab: while its setup isn't finished it opens on the next step to
   fill (Recipe standards, then Recipe); otherwise on its Consumer report. Replaces the address,
   so Back still goes where it was. */
function ProductLanding({ productId }) {
  useEffect(() => {
    let alive = true;
    const open = (tab) => alive && window.location.replace(`#/products/${productId}/${tab}`);
    getSetup()
      .then((d) => {
        const mine = (d?.items || []).find((i) => i.product.id === productId && !i.project);
        const next = mine?.next?.action;
        open(next?.kind === "product_tab" && next.product_id === productId ? next.tab : "report");
      })
      .catch(() => open("report"));
    return () => { alive = false; };
  }, [productId]);
  return <Loading />;
}

function ProductPage({ productId, tab, round, ReportView, project = null, onProjectChanged }) {
  const [state, reload] = useData(() => getOrgProduct(productId), [productId]);
  // Inside a project the tabs stay in the project's address (#/projects/3/recipe)
  const base = project ? `projects/${project.id}` : `products/${productId}`;
  const back = project ? ["projects", "Projects"] : ["products", "Products"];
  const tabs = project ? [["overview", "Project"], ...TABS] : TABS;
  const [notice, setNotice] = useState(null);
  const onNotice = setNotice; // React keeps the state setter stable, so no useCallback is needed

  if (state.loading && !state.data) return <Loading />;
  if (state.error) {
    return (
      <section>
        <button type="button" className="back-link" onClick={() => go(back[0])}><Icon name="back" /> {back[1]}</button>
        <ErrorPanel text={state.error} onRetry={reload} />
      </section>
    );
  }
  const detail = state.data;
  const { product, rounds, live_round: live } = detail;
  const failedStandards = detail.standards_failed || [];
  const shownRound = tab === "report" ? round || rounds[rounds.length - 1]?.number : null;
  // Recipe, Recipe standards and Survey questions: the current setup, or what an earlier survey used.
  const snapTab = SNAPSHOT_TABS.includes(tab);
  const snapRound = snapTab && round ? rounds.find((r) => r.number === round) || null : null;

  return (
    <section className="org-product" aria-labelledby="org-product-title">
      <button type="button" className="back-link" onClick={() => go(back[0])}><Icon name="back" /> {back[1]}</button>
      <header className="home-head org-product-head">
        <div className="page-heading">
          <p className="eyebrow">{project ? `${project.project_code} · ${product.category_name} · ${project.version_label}` : product.category_name}</p>
          <h1 id="org-product-title">
            {project ? project.project_name : product.name}
            {project && <span className={`status-chip ${PROJECT_STATUS_TONE[project.status]} project-status`}>{project.status_label}</span>}
          </h1>
          <p className="home-sub">
            {[project ? `Product: ${product.name}` : null, product.sku, detail.latest_version ? `Recipe v${detail.latest_version} is the latest` : "No recipe yet"].filter(Boolean).join(" · ")}
          </p>
        </div>
        <div className="org-head-actions">
          {tab === "report" && rounds.length > 1 && (
            <Select className="select" value={shownRound || ""} aria-label="Survey"
              onChange={(e) => go(`${base}/report/${e.target.value}`)}>
              {[...rounds].reverse().map((r) => (
                <option key={r.number} value={r.number}>Survey {r.number} ({r.status === "live" ? "collecting" : "completed"})</option>
              ))}
            </Select>
          )}
          {snapTab && rounds.length > 0 && (
            <Select className="select" value={snapRound ? String(snapRound.number) : ""} aria-label="Survey"
              onChange={(e) => go(`${base}/${tab}${e.target.value ? `/${e.target.value}` : ""}`)}>
              <option value="">Current (Recipe v{detail.latest_version || 1})</option>
              {[...rounds].reverse().map((r) => (
                <option key={r.number} value={String(r.number)}>Survey {r.number} · v{r.recipe_version ?? "—"} ({r.status === "live" ? "collecting" : "completed"})</option>
              ))}
            </Select>
          )}
          {tab === "report" && shownRound && <ReportDownloads productId={productId} round={shownRound} />}
          {/* Surveys start from the product version's project (Projects > Survey plan). */}
          <button type="button" className="btn btn-secondary" onClick={() => openProjectOf(product.id, detail.latest_version)}>
            Open project
          </button>
        </div>
      </header>

      {!project && <ProductSetupBar product={product} refreshKey={detail} />}
      <div className="page-tabs-u" role="tablist" aria-label={project ? project.project_name : product.name}>
        {tabs.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "is-on" : ""}
            onClick={() => go(`${base}/${key}`)}>
            {label}
          </button>
        ))}
      </div>

      <Notice notice={notice} onClose={() => setNotice(null)} />
      {!live && (
        <StandardsBlocked failures={failedStandards} version={detail.latest_version}
          onRecipe={tab === "standards" ? null : () => go(`${base}/standards`)} />
      )}

      {tab === "report" && <ReportTab product={product} rounds={rounds} round={shownRound} ReportView={ReportView} />}
      {snapTab && <SurveyBox survey={snapRound} rounds={rounds} latest={detail.latest_version} />}
      {snapTab && snapRound && <SurveySnapshot product={product} survey={snapRound} tab={tab} />}
      {tab === "recipe" && !snapRound && (
        <RecipeSection product={product} onNotice={onNotice} onSaved={() => reload({ silent: true })} />
      )}
      {tab === "standards" && !snapRound && <StandardsSection product={product} onChanged={() => reload({ silent: true })} />}
      {tab === "questions" && !snapRound && <QuestionsSection product={product} onNotice={onNotice} />}
      {tab === "rounds" && (
        <RoundsSection product={product} detail={detail} base={base} onStart={() => openProjectOf(product.id, detail.latest_version)}
          onChanged={() => { reload({ silent: true }); onProjectChanged?.(); }} onNotice={onNotice} />
      )}
      {tab === "compare" && <CompareSection product={product} rounds={rounds} base={base} />}
      {tab === "recording" && <RecordingAccessTab product={product} />}

    </section>
  );
}

/* ---------------- Projects ----------------
   At most one project per product VERSION (backend api/projects.py), made by the Super Admin
   on the Add project page: nothing creates one automatically when a product or recipe is saved. The product's
   standards, recipe plan and survey questions are set up in the Catalog; the project shows
   them linked, with the recipe of its version, and the survey rounds of its version. */

const PROJECT_STATUS_TONE = { draft: "is-neutral", ongoing: "is-ok", completed: "is-act" };

function ProjectsPage() {
  const [state, reload] = useData(() => listProjects(), []);
  const [q, setQ] = useState("");
  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  const { kpis, projects } = state.data;
  const query = q.trim().toLowerCase();
  const rows = projects.filter((p) => !query || `${p.project_code} ${p.project_name} ${p.category.name} ${p.product.name} ${p.version_label}`.toLowerCase().includes(query));
  const cards = [
    ["Total projects", kpis.total, "all projects"],
    ["Ongoing", kpis.ongoing, "survey collecting now"],
    ["Completed", kpis.completed, "surveys finished"],
    ["Draft", kpis.draft, "survey not started yet"],
  ];
  return (
    <section aria-labelledby="projects-title">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="projects-title">Projects</h1>
          <p className="home-sub">One project per product recipe, added by you: choose Add project, then the category, product and recipe to study.</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => go("projects/new")}>Add project</button>
      </header>
      <div className="home-stats project-kpis">
        {cards.map(([label, value, note], i) => (
          <div key={label} className={`h-stat kpi-card tone-${["brand", "pos", "teal", "act"][i]}`}>
            <KpiIcon name={["categories", "live", "analyzed", "draft"][i]} />
            <span className="h-stat-label">{label}</span>
            <span className="h-stat-value">{value}</span>
            <span className="h-stat-note">{note}</span>
          </div>
        ))}
      </div>
      <section className="panel home-products">
        {projects.length === 0 ? (
          <Empty title="No projects yet">
            Choose Add project to study a product recipe. Projects are only made here, never automatically.
          </Empty>
        ) : (
          <>
            <div className="table-toolbar">
              <input type="search" className="search" placeholder="Search projects, categories, products or versions" value={q}
                onChange={(e) => setQ(e.target.value)} aria-label="Search projects" />
            </div>
            <div className="table-wrap">
              <table className="data-table">
                <caption className="visually-hidden">Projects</caption>
                <thead>
                  <tr>
                    <th scope="col">Project</th>
                    <th scope="col" className="col-category">Category</th>
                    <th scope="col">Product</th>
                    <th scope="col">Recipe</th>
                    <th scope="col">Status</th>
                    <th scope="col">Survey</th>
                    <th scope="col" className="col-category">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.id} className="is-clickable" onClick={() => go(`projects/${p.id}`)}>
                      <td>
                        <div className="cell-product">
                          <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); go(`projects/${p.id}`); }}>{p.project_name}</button>
                          <span>{p.project_code}</span>
                        </div>
                      </td>
                      <td className="col-category">{p.category.name}</td>
                      <td>
                        <div className="cell-product">
                          <strong>{p.product.name}</strong>
                          <span>{p.product.sku || (p.product.is_active ? "" : "Inactive")}</span>
                        </div>
                      </td>
                      <td>
                        <span className={`pv-chip${p.is_latest_version ? " is-latest" : ""}`}
                          title={p.is_latest_version ? "The product's latest version" : `Older version: the latest is v${p.latest_version}`}>
                          {p.version_label}
                        </span>
                      </td>
                      <td><span className={`status-chip ${PROJECT_STATUS_TONE[p.status]}`}>{p.status_label}</span></td>
                      <td>
                        {p.live_round ? (
                          <span className="round-chip is-live">Survey {p.live_round.number} · {p.live_round.done} of {p.live_round.target}</span>
                        ) : p.rounds ? <span className="muted">{plural(p.rounds, "survey")} done</span> : <span className="muted">Not started</span>}
                      </td>
                      <td className="col-category">
                        <div className="cell-product"><strong>{fmtDate(p.created_at)}</strong><span>{p.created_by || ""}</span></div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!rows.length && <p className="muted table-empty">No projects match “{q}”.</p>}
          </>
        )}
      </section>
    </section>
  );
}

/* Add a project: a page (not a popup), the form on the left and, once a product and its
   recipe are chosen, a short overview of that product recipe on the right: what's in the
   recipe, its standards, survey questions, plan and earlier surveys. */
function AddProjectPage({ prefill = null }) {
  const [options, setOptions] = useState(null);
  const [form, setForm] = useState({ project_name: "", description: "", category_id: "", product_id: "", product_version: "", total_target: "" });
  const [targetTouched, setTargetTouched] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState({ loading: false, data: null, error: "" });
  const [questionsKey, setQuestionsKey] = useState(0); // new value = the overview (question count) reloads
  const [notice, setNotice] = useState(null);
  // Survey plans made on this page: kept here and added to the project when it is created
  const [plans, setPlans] = useState([]);
  const [planOpen, setPlanOpen] = useState(false); // true = new plan, a number = editing that pending plan
  // One page per step: 1 project details -> 2 survey plans -> 3 survey questions (Back keeps everything)
  const [step, setStep] = useState(1);
  const toStep = (n) => {
    setStep(n);
    if (n === 3) setQuestionsKey((k) => k + 1); // the question count is read again (questions may have changed elsewhere)
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  useEffect(() => {
    projectOptions().then((d) => {
      setOptions(d.categories);
      if (!prefill?.productId) return;
      // From the product's setup: its category and product are chosen (and fixed) already
      const cat = d.categories.find((c) => c.products.some((p) => p.id === prefill.productId));
      const prod = cat?.products.find((p) => p.id === prefill.productId);
      if (!prod) {
        setError("This product's recipe already has a project (or the product isn't active). Open it from Projects.");
        return;
      }
      const v = prefill.version && prod.versions.includes(prefill.version) ? prefill.version : prod.versions[prod.versions.length - 1];
      setForm((f) => ({ ...f, category_id: String(cat.id), product_id: String(prod.id), product_version: v ? String(v) : "" }));
    }).catch((e) => setError(orgError(e, "The categories couldn't be loaded.")));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const fixedChoice = Boolean(prefill?.productId);
  const category = (options || []).find((c) => String(c.id) === String(form.category_id));
  const product = (category?.products || []).find((p) => String(p.id) === String(form.product_id));
  useEffect(() => {
    if (!form.product_id) return undefined;
    let alive = true;
    setSummary((s) => ({ ...s, loading: true, error: "" }));
    getProductSummary(form.product_id, form.product_version || null)
      .then((d) => {
        if (!alive) return;
        setSummary({ loading: false, data: d, error: "" });
        // Start from the product's planned responses until the Super Admin types their own.
        if (!targetTouched && d?.plan?.target) setForm((f) => ({ ...f, total_target: String(d.plan.target) }));
      })
      .catch((e) => alive && setSummary({ loading: false, data: null, error: orgError(e, "The product overview couldn't be loaded.") }));
    return () => { alive = false; };
  }, [form.product_id, form.product_version, questionsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k) => (e) => setForm((f) => {
    const next = { ...f, [k]: e.target.value };
    if (k === "category_id") Object.assign(next, { product_id: "", product_version: "" });
    if (k === "product_id") {
      const p = (category?.products || []).find((x) => String(x.id) === e.target.value);
      next.product_version = p?.versions?.length ? String(p.versions[p.versions.length - 1]) : "";
    }
    return next;
  });
  const targetProblem = form.total_target.trim() ? wholeNumberProblem(form.total_target, "Total consumer responses") : "";
  // The product's survey questions must be ready (5-10) before its project can be created.
  const questionCount = summary.data?.product?.id === Number(form.product_id) ? summary.data?.questions?.count ?? null : null;
  const questionsOk = questionCount != null && questionCount >= 5 && questionCount <= 10;
  // In order: project details -> survey plans -> survey questions -> Create project
  const detailsOk = form.project_name.trim().length >= 2 && form.category_id && form.product_id && form.product_version
    && form.total_target.trim() && !targetProblem;
  const planned = plans.reduce((n, pl) => n + (Number(pl.target) || 0), 0);
  const ready = detailsOk && plans.length > 0 && questionsOk;
  const submit = async () => {
    if (!ready || busy) return;
    setBusy(true);
    setError("");
    try {
      const p = await createProject({ ...form, category_id: Number(form.category_id), product_id: Number(form.product_id),
        product_version: Number(form.product_version), total_target: Number(form.total_target) });
      for (const pl of plans) await addProjectPlan(p.id, pl); // the survey plans made above
      go(`projects/${p.id}`);
    } catch (e) {
      setError(orgError(e, "The project couldn't be created."));
      setBusy(false);
    }
  };
  return (
    <section className="add-project" aria-labelledby="add-project-title">
      <button type="button" className="back-link" onClick={() => go("projects")}><Icon name="back" /> Projects</button>
      <header className="home-head">
        <div className="page-heading">
          <h1 id="add-project-title">Add a project</h1>
          <p className="home-sub">Choose the product and the recipe to study: each product recipe can have one project.</p>
        </div>
      </header>
      <ol className="wizard-steps" aria-label="Steps">
        {[[1, "Project details"], [2, "Survey plans"], [3, "Survey questions"]].map(([n, label]) => (
          <li key={n} className={step === n ? "is-current" : step > n ? "is-done" : ""} aria-current={step === n ? "step" : undefined}>
            <button type="button" disabled={n > step} onClick={() => toStep(n)}>
              <span className="wizard-num" aria-hidden="true">{step > n ? "✓" : n}</span>{label}
            </button>
          </li>
        ))}
      </ol>
      {step === 1 && (
      <div className="add-project-grid">
        <section className="panel add-project-form" aria-label="Project details">
          <label className="field">
            <span className="field-label">Project name <span className="req" aria-hidden="true">*</span></span>
            <input className="input" maxLength={200} value={form.project_name} onChange={set("project_name")} placeholder="e.g. VitaBoost Lemon Mint launch study" autoFocus />
          </label>
          <label className="field">
            <span className="field-label">Description</span>
            <textarea className="input" rows={3} maxLength={4000} value={form.description} onChange={set("description")} placeholder="e.g. Taste test in Delhi and Mumbai before the national launch" />
          </label>
          <div className="field-row is-three">
          <div className="field">
            <span className="field-label" id="ap-cat">Category <span className="req" aria-hidden="true">*</span></span>
            <Select className="select" value={form.category_id} onChange={set("category_id")} disabled={!options || fixedChoice} aria-labelledby="ap-cat">
              <option value="">{options ? "Choose a category" : "Loading…"}</option>
              {/* only categories with a product recipe still to plan (one project per product recipe) */}
              {(options || []).filter((c) => c.products.length).map((c) => (
                <option key={c.id} value={c.id}>{c.name} ({c.products.length})</option>
              ))}
            </Select>
          </div>
            <div className="field">
              <span className="field-label" id="ap-prod">Product <span className="req" aria-hidden="true">*</span></span>
              <Select className="select" value={form.product_id} onChange={set("product_id")} disabled={!category || fixedChoice} aria-labelledby="ap-prod">
                <option value="">{category ? "Choose a product" : "Choose a category first"}</option>
                {(category?.products || []).map((p) => <option key={p.id} value={p.id}>{p.name}{p.sku ? ` · ${p.sku}` : ""}</option>)}
              </Select>
            </div>
            <div className="field">
              <span className="field-label" id="ap-recipe">Recipe <span className="req" aria-hidden="true">*</span></span>
              <Select className="select" value={form.product_version} onChange={set("product_version")} disabled={!product} aria-labelledby="ap-recipe">
                <option value="">{product ? "Choose a recipe" : "Choose a product first"}</option>
                {(product?.versions || []).map((v) => <option key={v} value={v}>Recipe v{v}</option>)}
              </Select>
            </div>
          </div>
          <p className="field-hint">
            {fixedChoice ? "Category and product come from the product's setup." : "Only recipes without a project are listed: each product recipe has one project."}
          </p>
          <label className="field add-project-target">
            <span className="field-label">Total consumer responses <span className="req" aria-hidden="true">*</span></span>
            <input className={`input${targetProblem ? " is-invalid" : ""}`} type="text" inputMode="numeric" autoComplete="off" value={form.total_target}
              placeholder="e.g. 100" aria-invalid={Boolean(targetProblem)}
              onKeyDown={(e) => { if (["e", "E", "+", "-", ".", ","].includes(e.key)) e.preventDefault(); }}
              onChange={(e) => { setTargetTouched(true); setForm((f) => ({ ...f, total_target: e.target.value })); }} />
            {targetProblem ? <span className="field-error">{targetProblem}</span>
              : <span className="field-hint">The survey plans share this target.</span>}
          </label>
        </section>
        <ProductOverview summary={summary} chosen={Boolean(form.product_id)} />
      </div>
      )}
      <Notice notice={notice} onClose={() => setNotice(null)} />
      {/* Step 2: the survey plans, as cards (like the project page) */}
      {step === 2 && (
        <section className="panel" aria-labelledby="ap-plans-title">
          <div className="ap-head">
            <div>
              <h2 id="ap-plans-title" className="report-h">Survey plans</h2>
              <p className="muted small">{plans.length ? `${plans.length} plan${plans.length === 1 ? "" : "s"} · ${planned} of ${form.total_target} consumers planned. The Admin allocates each plan's team after the project is created.`
                : "Add at least one plan: how many consumers and when."}</p>
            </div>
            <button type="button" className="btn btn-primary btn-sm" onClick={() => setPlanOpen(true)}
              disabled={planned >= Number(form.total_target)}>Add survey plan</button>
          </div>
          {plans.length === 0 ? <p className="muted">No survey plan yet.</p> : (
            <div className="ap-grid">
              {plans.map((pl, i) => {
                const code = `PLA${String(i + 1).padStart(3, "0")}`;
                const card = { ...pl, id: `draft-${i}`, code, name: code, done: 0, cities: [], members: [], allocation_complete: false,
                  target: Number(pl.target) || 0 };
                return (
                  <PlanCard key={code} p={card} canEdit canAllocate={false}
                    onEdit={() => setPlanOpen(i)} onDelete={() => setPlans(plans.filter((_, k) => k !== i))} />
                );
              })}
            </div>
          )}
        </section>
      )}
      {planOpen !== false && (  // true = a new plan; 0, 1 … = editing that pending plan
        <PlanDialog productId={product?.id} title="Add a survey plan" total={Number(form.total_target)}
          plan={typeof planOpen === "number" ? { ...plans[planOpen], name: `PLA${String(planOpen + 1).padStart(3, "0")}` } : undefined}
          limit={Number(form.total_target) - planned + (typeof planOpen === "number" ? Number(plans[planOpen].target) || 0 : 0)}
          nextCode={`PLA${String(plans.length + 1).padStart(3, "0")}`}
          create={(body) => Promise.resolve(body)} update={(body) => Promise.resolve(body)}
          onClose={() => setPlanOpen(false)}
          onSaved={(body) => {
            setPlans(typeof planOpen === "number" ? plans.map((x, k) => (k === planOpen ? body : x)) : [...plans, body]);
            setPlanOpen(false);
          }} />
      )}
      {/* Step 3: the survey questions (or a template) */}
      {step === 3 && product && (
        <ProjectQuestionsStep product={{ id: product.id, name: product.name }} onNotice={setNotice}
          onChanged={() => setQuestionsKey((k) => k + 1)} />
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="add-project-actions add-project-footer">
        <span className="field-hint add-project-why">
          {step === 1 && !detailsOk ? "Fill in the project details to continue."
            : step === 2 && !plans.length ? "Add at least one survey plan to continue."
            : step === 3 && !questionsOk ? (questionCount != null && questionCount > 10 ? "Keep at most 10 survey questions."
              : `Set the survey questions (5–10)${questionCount != null ? `: ${Math.max(0, 5 - questionCount)} more needed` : ""}, or use a template.`)
            : ""}
        </span>
        {step === 1
          ? <button type="button" className="btn btn-secondary" onClick={() => go("projects")}>Cancel</button>
          : <button type="button" className="btn btn-secondary" onClick={() => toStep(step - 1)}>← Back</button>}
        {step === 1 && <button type="button" className="btn btn-primary" disabled={!detailsOk} onClick={() => toStep(2)}>Next: Survey plans →</button>}
        {step === 2 && <button type="button" className="btn btn-primary" disabled={!plans.length} onClick={() => toStep(3)}>Next: Survey questions →</button>}
        {step === 3 && <button type="button" className="btn btn-primary" onClick={submit} disabled={!ready || busy}>{busy ? "Creating…" : "Create project"}</button>}
      </div>
    </section>
  );
}


/* Survey questions of the chosen product, set while creating (or editing) its project: one shared
   set per product, 5-10 questions, saved as they are changed. A project is created only with them. */
function ProjectQuestionsStep({ product, onChanged, onNotice, readOnly = false }) {
  // Reusable question sets: copy a template's (or an earlier product's) questions in, or save these
  const [dialog, setDialog] = useState(null);
  const [version, setVersion] = useState(0); // new value = the questions below reload
  const [count, setCount] = useState(0);
  useEffect(() => {
    getProductSummary(product.id).then((d) => setCount(d?.questions?.count ?? 0)).catch(() => {});
  }, [product.id, version]);
  const done = (text) => {
    setDialog(null);
    setVersion((v) => v + 1);
    onChanged?.();
    onNotice?.({ tone: "ok", text });
  };
  return (
    <section className="panel project-questions-step" aria-label="Survey questions">
      <div className="qt-head">
        <p className="eyebrow">Survey questions</p>
        {!readOnly && (
          <span className="head-actions">
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setDialog("use")}>Use existing question template</button>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setDialog("save")} disabled={!count}
              title={count ? undefined : "Add questions first"}>Save as template</button>
          </span>
        )}
      </div>
      {dialog === "use" && <UseTemplateDialog product={product} currentCount={count} onClose={() => setDialog(null)} onApplied={done} />}
      {dialog === "save" && <SaveTemplateDialog product={product} onClose={() => setDialog(null)} onSaved={done} />}
      <p className="muted small project-questions-note">
        What survey persons ask each consumer of {product.name}. Set them here before creating the project; they are shared by
        every project of this product and are saved as you change them.
      </p>
      <QuestionsTab key={`${product.id}-${version}`} product={product} onNotice={onNotice} readOnly={readOnly}
        onChanged={() => { setVersion((v) => v); onChanged?.(); getProductSummary(product.id).then((d) => setCount(d?.questions?.count ?? 0)).catch(() => {}); }} />
    </section>
  );
}

/* "About this product": a few short bullets per part, enough to understand the product. */
// A short list ("+9 more" opens the rest, "Show less" folds it again).
function MoreList({ items, show = 4, render = (x) => x, keyOf = (x) => String(x) }) {
  const [open, setOpen] = useState(false);
  const visible = open ? items : items.slice(0, show);
  return (
    <ul>
      {visible.map((x) => <li key={keyOf(x)}>{render(x)}</li>)}
      {items.length > show && (
        <li className="po-more-item">
          <button type="button" className="link-btn po-more" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? "Show less" : `+${items.length - show} more`}
          </button>
        </li>
      )}
    </ul>
  );
}

function ProductOverview({ summary, chosen }) {
  if (!chosen) {
    return (
      <aside className="panel product-overview is-empty" aria-label="About this product">
        <h2 className="report-h">About this product</h2>
        <p className="muted">Choose a category, product and recipe: a short overview appears here, with what's in the recipe, its standards and survey questions.</p>
      </aside>
    );
  }
  if (summary.loading && !summary.data) return <aside className="panel product-overview" aria-label="About this product"><p className="muted">Loading the overview…</p></aside>;
  if (summary.error) return <aside className="panel product-overview" aria-label="About this product"><p className="form-error">{summary.error}</p></aside>;
  const d = summary.data;
  if (!d) return null;
  const { product, recipe: r, standards: st, questions: q } = d;
  const made = fmtDateTime(r.created_at || r.saved_at);
  return (
    <aside className="panel product-overview" aria-label={`About ${product.name}`}>
      <p className="eyebrow">{product.category}{product.sku ? ` · ${product.sku}` : ""}</p>
      <h2 className="report-h">
        {product.name} · Recipe v{r.version}
        {made && <span className="po-made">Created {made}</span>}
      </h2>
      {product.description && <p className="po-desc">{product.description}</p>}
      <dl className="po-list">
        <div>
          <dt>Recipe</dt>
          <dd>
            {r.has_recipe ? (
              <>
                <span>{plural(r.ingredient_count, "ingredient")} · {plural(r.steps, "step")}{r.batch_size ? ` · batch ${r.batch_size}` : ""}</span>
                <MoreList items={r.ingredients} show={6} keyOf={(i) => i.name}
                  render={(i) => <>{i.name}{i.amount ? <span className="muted"> · {i.amount}</span> : null}</>} />
                {r.saved_at && <span className="muted small">Saved {fmtDate(r.saved_at)}{r.saved_by ? ` by ${r.saved_by}` : ""}{r.changes.length ? ` · ${r.changes[0]}` : ""}</span>}
              </>
            ) : <span className="muted">No recipe yet: add it before the survey starts.</span>}
          </dd>
        </div>
        <div>
          <dt>Recipe standards</dt>
          <dd>
            {st.count ? (
              <>
                <span className={st.failed ? "po-warn" : "po-ok"}>{st.failed ? `${st.failed} of ${st.count} not met` : `${plural(st.count, "standard")}, all met`}</span>
                <MoreList items={st.names} show={4} />
              </>
            ) : <span className="muted">None set</span>}
          </dd>
        </div>
        <div>
          <dt>Survey questions</dt>
          <dd>
            {q.count ? (
              <>
                <span className={q.count < 5 ? "po-warn" : ""}>{plural(q.count, "question")}{q.count < 5 ? " · surveys need at least 5" : ""}</span>
                <MoreList items={q.sample} show={3} />
              </>
            ) : <span className="po-warn">No questions yet: surveys need at least 5.</span>}
          </dd>
        </div>
      </dl>
    </aside>
  );
}

/* Edit project: the same page and layout as Add project. Only the name and description change;
   the project code, category, product and recipe are shown but fixed (a new recipe gets its own project). */
function EditProjectPage({ projectId }) {
  const [project, setProject] = useState(null);
  const [form, setForm] = useState({ project_name: "", description: "", total: "" });
  const [summary, setSummary] = useState({ loading: true, data: null, error: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const loadSummary = (p) => getProductSummary(p.product.id, p.product_version)
    .then((d) => setSummary({ loading: false, data: d, error: "" }))
    .catch((e) => setSummary({ loading: false, data: null, error: orgError(e, "The product overview couldn't be loaded.") }));
  useEffect(() => {
    getProject(projectId).then((p) => {
      setProject(p);
      setForm({ project_name: p.project_name, description: p.description || "", total: p.total_target != null ? String(p.total_target) : "" });
      loadSummary(p);
    }).catch((e) => setError(orgError(e, "The project couldn't be loaded.")));
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!project) return error ? <ErrorPanel text={error} /> : <Loading />;
  const submit = async () => {
    if (form.project_name.trim().length < 2) return setError("Enter the project name (at least 2 characters).");
    const totalChanged = !project.closed && form.total.trim() !== (project.total_target != null ? String(project.total_target) : "");
    if (totalChanged) {
      const p = wholeNumberProblem(form.total, "Total consumer responses");
      if (p) return setError(p);
    }
    setBusy(true);
    setError("");
    try {
      // The total first: the server refuses it below what's collected or planned (nothing else is saved then).
      if (totalChanged) await setProjectTarget(project.id, Number(form.total.trim()));
      await updateProject(project.id, { project_name: form.project_name.trim(), description: form.description.trim() || null });
      go(`projects/${project.id}`);
    } catch (e) {
      setError(orgError(e, "The project couldn't be saved."));
      setBusy(false);
    }
  };
  const fixed = (label, value) => (
    <div className="field">
      <span className="field-label">{label}</span>
      <input className="input" value={value} readOnly disabled aria-readonly="true" />
    </div>
  );
  return (
    <section className="add-project" aria-labelledby="edit-project-title">
      <button type="button" className="back-link" onClick={() => go(`projects/${project.id}`)}><Icon name="back" /> {project.project_code}</button>
      <header className="home-head">
        <div className="page-heading">
          <h1 id="edit-project-title">Edit project</h1>
          <p className="home-sub">Change the name, description and total consumer responses. The code, category, product and recipe stay as they are.</p>
        </div>
      </header>
      <div className="add-project-grid">
        <section className="panel add-project-form" aria-label="Project details">
          <div className="field-row">
            {fixed("Project code", project.project_code)}
            {fixed("Category code", project.category.code || "—")}
          </div>
          <label className="field">
            <span className="field-label">Project name <span className="req">*</span></span>
            <input className="input" maxLength={200} value={form.project_name} autoFocus onChange={(e) => setForm({ ...form, project_name: e.target.value })} />
          </label>
          <label className="field">
            <span className="field-label">Description</span>
            <textarea className="input" rows={3} maxLength={4000} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
          <div className="field-row is-three">
            {fixed("Category", project.category.name)}
            {fixed("Product", project.product.name)}
            {fixed("Recipe", project.version_label)}
          </div>
          <label className="field add-project-target">
            <span className="field-label">Total consumer responses <span className="req">*</span></span>
            {project.closed ? (
              <input className="input" value={project.total_target ?? "Not set"} readOnly disabled />
            ) : (
              <input className="input" type="text" inputMode="numeric" autoComplete="off" value={form.total} placeholder="e.g. 30"
                onKeyDown={(e) => { if (["e", "E", "+", "-", ".", ","].includes(e.key)) e.preventDefault(); }}
                onChange={(e) => setForm({ ...form, total: e.target.value })} />
            )}
            <span className="field-hint">
              {project.closed ? "This project has ended: its total can't change."
                : "Not below the responses already collected or planned. Raised after a completed survey, that survey reopens for the extra responses."}
            </span>
          </label>
        </section>
        <ProductOverview summary={summary} chosen />
      </div>
      <Notice notice={notice} onClose={() => setNotice(null)} />
      <ProjectQuestionsStep product={{ id: project.product.id, name: project.product.name }} onNotice={setNotice}
        onChanged={() => loadSummary(project)} readOnly={project.closed} />
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="add-project-actions add-project-footer">
        <button type="button" className="btn btn-secondary" onClick={() => go(`projects/${project.id}`)}>Cancel</button>
        <button type="button" className="btn btn-primary" onClick={submit} disabled={busy}>{busy ? "Saving…" : "Save changes"}</button>
      </div>
    </section>
  );
}

/* The project page: its details and its Survey plan (action plans), from where the Super Admin
   starts the survey of the project's product version. Surveys start only here. */
function ProjectPage({ projectId }) {
  const [state, reload] = useData(() => Promise.all([getProject(projectId), getProjectPlans(projectId)]), [projectId]);
  const [dialog, setDialog] = useState(null); // {type: "plan", plan?} | {type: "start"} | {type: "edit"}
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState("");
  if (state.loading && !state.data) return <Loading />;
  if (state.error) {
    return (
      <section>
        <button type="button" className="back-link" onClick={() => go("projects")}><Icon name="back" /> Projects</button>
        <ErrorPanel text={state.error} onRetry={reload} />
      </section>
    );
  }
  const [project, plan] = state.data;
  const refresh = () => reload({ silent: true });
  const live = plan.stage === "live";
  const total = plan.total_target; // set on the product: the survey's plans may not add up to more
  // After an ended survey the next one may plan only what the total was raised by.
  const used = plan.used || 0;
  const reopenAbove = plan.reopen_above || used; // a completed survey reopens above this target
  const room = plan.available ?? total; // what this survey's plans may add up to
  const closed = plan.stage === "ended" && plan.can_edit && plan.can_add_plans === false; // raise the total to reopen it
  const over = room != null && plan.planned > room;
  const limitFor = (p) => (room == null ? null : Math.max(0, room - plan.planned + (p?.target || 0)));

  const removePlan = async (p) => {
    const ok = await confirmDialog({ title: `Delete “${p.name}”?`, message: "Its team allocation is removed too.", confirmLabel: "Delete", danger: true });
    if (!ok) return;
    try {
      await deleteActionPlan(p.id);
      refresh();
    } catch (e) {
      setError(orgError(e, "The plan couldn't be deleted."));
    }
  };
  const plansFirst = (names) => confirmDialog({
    title: names.length === 1 ? "Delete the active survey plan first" : "Delete the active survey plans first",
    message: `To delete ${project.project_code}, first delete its active survey plan${names.length === 1 ? "" : "s"}: `
      + `${names.join(", ")}. Then delete the project.`,
    confirmLabel: "OK",
    cancelLabel: null,
  });
  const endThisProject = async () => {
    const ok = await confirmDialog({
      title: `End ${project.project_code}?`,
      message: "Its collecting survey ends now and no more responses are accepted. Nothing more can be planned or started in this project. "
        + "Every response already collected is kept for review.",
      confirmLabel: "End project",
      danger: true,
    });
    if (!ok) return;
    try {
      await endProject(project.id);
      setNotice({ tone: "ok", text: `${project.project_code} has ended. Its responses are kept.` });
      refresh();
    } catch (e) {
      setError(orgError(e, "The project couldn't be ended."));
    }
  };
  const removeProject = async () => {
    if (plan.plans.length) {
      await plansFirst(plan.plans.map((p) => p.name));
      return;
    }
    const ok = await confirmDialog({
      title: `Delete ${project.project_code}?`,
      message: `“${project.project_name}” is deleted${plan.round ? `, with Survey ${plan.round.number} (no consumer feedback yet). It also leaves the Admin dashboard` : ""}. `
        + `The product ${project.product.name} and its recipe are not changed.`,
      confirmLabel: "Delete project",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteProject(project.id);
      go("projects");
    } catch (e) {
      const d = e?.response?.data?.detail;
      if (d?.code === "plans_exist") return plansFirst(d.plans || []);
      setError(orgError(e, "The project couldn't be deleted."));
    }
  };

  return (
    <section className="org-product project-page" aria-labelledby="project-title">
      <button type="button" className="back-link" onClick={() => go("projects")}><Icon name="back" /> Projects</button>
      <header className="home-head org-product-head">
        <div className="page-heading">
          <p className="eyebrow">{project.project_code} · {project.category.name} · {project.version_label}</p>
          <h1 id="project-title">
            {project.project_name}
            <span className={`status-chip ${PROJECT_STATUS_TONE[project.status]} project-status`}>{project.status_label}</span>
          </h1>
          <p className="home-sub">
            Product: {project.product.name}{project.product.sku ? ` · ${project.product.sku}` : ""} · version {project.product_version}
          </p>
        </div>
        <div className="org-head-actions">
          <button type="button" className="btn btn-secondary" onClick={() => go(`products/${project.product.id}`)}>Open product</button>
          <button type="button" className="btn btn-secondary" onClick={() => go(`projects/${project.id}/edit`)}>Edit</button>
          {plan.can_end && !project.closed && (
            <button type="button" className="btn btn-secondary acct-danger" onClick={endThisProject}>End project</button>
          )}
          {/* No Delete at all once consumer feedback has come in (the server refuses it too). */}
          {project.can_delete && (
            <button type="button" className="btn btn-secondary acct-danger" onClick={removeProject}>Delete project</button>
          )}
        </div>
      </header>
      <Notice notice={notice} onClose={() => setNotice(null)} />
      {error && <p className="form-error" role="alert">{error}</p>}

      <section className="panel project-summary">
        {project.description && <p className="project-desc">{project.description}</p>}
        <dl className="ap-facts project-facts">
          <div><dt>Project code</dt><dd>{project.project_code}</dd></div>
          <div><dt>Category</dt><dd>{project.category.name}{project.category.code ? ` · ${project.category.code}` : ""}</dd></div>
          <div><dt>Product</dt><dd>{project.product.name}{project.product.is_active ? "" : " (inactive)"}</dd></div>
          <div>
            <dt>Product version</dt>
            <dd>{project.version_label}{project.version?.saved_at ? ` · saved ${fmtDate(project.version.saved_at)}${project.version.saved_by ? ` by ${project.version.saved_by}` : ""}` : ""}</dd>
          </div>
          <div><dt>Total consumer responses</dt><dd>{total ?? "Not set"}</dd></div>
        </dl>
      </section>

      <section className="panel" aria-labelledby="recipe-plan-title">
        <div className="ap-head">
          <div>
            <h2 id="recipe-plan-title" className="report-h">
              Survey plan
              {plan.round ? <span className={`round-chip${live ? " is-live" : ""}`}>Survey {plan.round.number} · {live ? "collecting" : "completed"}</span> : null}
            </h2>
            <p className="muted">
              {live
                ? `${plural(plan.plans.length, "plan")} · ${plan.round.done} of ${plan.round.target} consumers surveyed. The Admin allocates each plan's team.`
                : closed
                  ? `Survey ${plan.round.number} is completed. To survey again, set the response target above ${reopenAbove} (Edit response target): Survey ${plan.round.number} reopens for the extra responses.`
                  : plan.plans.length
                  ? `${plural(plan.plans.length, "plan")} · ${plan.target} consumers in total. Start the survey when the plan is ready.`
                  : "Plan the survey: how many consumers, which cities and when. Add one plan per team or city."}
            </p>
          </div>
          {plan.can_edit && (
            <span className="ap-actions">
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setDialog({ type: "total" })}>Edit response target</button>
              {!closed && (
                <button type="button" className="btn btn-primary btn-sm" onClick={() => setDialog({ type: "plan" })}
                  disabled={room != null && limitFor(null) === 0}
                  title={room != null && limitFor(null) === 0 ? "The response target is used up: raise it first" : ""}>
                  Add survey plan
                </button>
              )}
            </span>
          )}
        </div>
        {(total == null || over || plan.stage === "closed") && (
          <div className={`plan-total${over ? " is-over" : ""}`} role="status">
            <span>
              {plan.stage === "closed" ? "This project has ended. Its surveys and responses are kept for review."
                : over ? "The plans are above the response target: lower a plan."
                  : "No response target set for this project yet. Set it with Edit response target, so plans can't exceed it."}
            </span>
          </div>
        )}
        <div className="ap-grid">
          {plan.plans.map((p) => (
            <PlanCard key={p.id} p={p} canEdit={plan.can_edit && (live || !p.round_id)} canAllocate={false}
              onEdit={() => setDialog({ type: "plan", plan: p })} onDelete={() => removePlan(p)} />
          ))}
          {!plan.plans.length && !closed && <p className="muted">No survey plan yet.</p>}
        </div>
        {/* A completed survey: the line under the heading already says how to reopen it. */}
        {!live && !closed && (
          <div className="org-callout project-start">
            <span>{plan.start_problem || `Ready: ${plan.target}${total ? ` of ${total}` : ""} consumers planned across ${plural(plan.plans.length, "plan")}. You can add plans for the rest while the survey collects.`}</span>
            {/* A completed survey reopens by raising the response target, so no Start button then. */}
            {!closed && (
              <button type="button" className="btn btn-primary btn-sm" disabled={!plan.can_start} onClick={() => setDialog({ type: "start" })}>
                Start survey
              </button>
            )}
          </div>
        )}
        {live && (
          <div className="org-callout">
            <span>Survey {plan.round.number} is collecting responses. See them and end the survey on the product's Survey runs tab.</span>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => go(`products/${project.product.id}/rounds`)}>Open survey runs</button>
          </div>
        )}
      </section>

      {dialog?.type === "plan" && (
        <PlanDialog productId={project.product.id} plan={dialog.plan} title="Add a survey plan"
          limit={limitFor(dialog.plan)} total={total} nextCode={nextPlanCode(plan.plans)}
          create={(body) => addProjectPlan(project.id, body)}
          onClose={() => setDialog(null)} onSaved={() => { setDialog(null); refresh(); }} />
      )}
      {dialog?.type === "total" && (
        <TargetDialog project={project} total={total} planned={plan.planned} used={used} onClose={() => setDialog(null)}
          onSaved={(saved) => {
            setDialog(null);
            if (saved?.reopened) {
              setNotice({ tone: "ok", text: `Survey ${saved.reopened.number} reopened: it is collecting again, now ${saved.reopened.target} consumers in total. The same team collects the extra responses.` });
            }
            refresh();
          }} />
      )}
      {dialog?.type === "start" && (
        <StartSurveyDialog project={project} plan={plan} onClose={() => setDialog(null)}
          onStarted={(r) => {
            setDialog(null);
            setNotice({ tone: "ok", text: `Survey ${r.number} started with ${r.target} consumers. The research team has been told.` });
            refresh();
          }} />
      )}
    </section>
  );
}

/* Edit response target: the project's total consumer responses (Super Admin only). Never below
   what's already collected or committed to its survey plans; raised after its survey completed,
   that survey reopens for the extra responses (backend PUT /org/projects/{id}/target). */
function TargetDialog({ project, total, planned, used = 0, onClose, onSaved }) {
  const [value, setValue] = useState(total != null ? String(total) : "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const collected = project.responses || 0;
  const floor = Math.max(collected, used + planned, 1);
  const problem = value.trim() ? wholeNumberProblem(value, "The response target") : "";
  const n = Number(value);
  const save = async () => {
    const p = wholeNumberProblem(value, "The response target");
    if (p) return setError(p);
    if (n < floor) return setError(`The response target can't go below ${floor}: ${collected >= used + planned ? `${collected} responses already collected` : `${used + planned} responses already committed to survey plans`}.`);
    setBusy(true);
    setError("");
    try {
      onSaved(await setProjectTarget(project.id, n));
    } catch (e) {
      setError(orgError(e, "The response target couldn't be saved."));
      setBusy(false);
    }
  };
  return (
    <Modal title="Edit response target" subtitle={`${project.project_code} · ${project.product.name} recipe v${project.product_version}`}
      onClose={onClose}
      footer={<>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy || Boolean(problem) || !value.trim()} onClick={save}>{busy ? "Saving…" : "Save target"}</button>
      </>}>
      <label className="field">
        <span className="field-label">Response target</span>
        <input className={`input${problem ? " is-invalid" : ""}`} type="text" inputMode="numeric" autoComplete="off" value={value} autoFocus
          onKeyDown={(e) => { if (["e", "E", "+", "-", ".", ","].includes(e.key)) e.preventDefault(); }}
          onChange={(e) => { setValue(e.target.value); setError(""); }} placeholder="e.g. 100" />
        {problem && <span className="field-error">{problem}</span>}
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

/* From the product page: its version's project (where surveys are planned and started). */
async function openProjectOf(productId, version) {
  try {
    const { projects } = await listProjects();
    const mine = projects.filter((p) => p.product.id === productId);
    const match = mine.find((p) => p.product_version === version) || mine.sort((a, b) => b.product_version - a.product_version)[0];
    go(match ? `projects/${match.id}` : "projects");
  } catch {
    go("projects");
  }
}

/* ---------------- Recording access ----------------
   Consumer recordings are encrypted (backend services/recording_crypto.py). The Super Admin has ONE
   recording passkey for the whole organization (stored only as a hash: shown here once, when set),
   the same for every product, and chooses per product the Admins allowed to play its recordings.
   The passkey can be sent to them inside the app (each Admin sees it once). Changing it never makes
   recordings unplayable. */

const OUTCOME = { granted: "Opened", played: "Played", wrong_passkey: "Wrong passkey", not_authorised: "Not allowed",
  locked: "Locked (too many tries)", no_passkey: "No passkey set", expired: "Link expired" };

function RecordingAccessTab({ product }) {
  const [state, reload] = useData(() => recordingAccessHistory(product.id), [product.id]);
  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  const { history } = state.data;

  const ok = (h) => ["granted", "played"].includes(h.outcome);
  const ROLE = { admin: "Admin", client: "Super Admin", executive: "Survey person" };
  const initials = (n) => (n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");

  return (
    <div className="rec-access">
      <div className="rec-banner">
        <SectionIcon name="standards" />
        <div>
          <strong>Recordings are stored encrypted</strong>
          <span>You and every Admin play them from the analysis page; a survey person plays their own only with the recording passkey. Every attempt is logged below.</span>
        </div>
      </div>
      <section className="panel rc-tone rec-panel">
        <header className="recipe-card-head">
          <div>
            <h3><SectionIcon name="play" />Who played the recordings of {product.name}</h3>
            <p className="muted">Newest first.</p>
          </div>
        </header>
        {!history.length ? <p className="muted rec-empty">Nobody has played a recording of this product yet.</p> : (
          <div className="table-wrap">
            <table className="data-table rec-table">
              <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">Recording</th><th scope="col">Result</th></tr></thead>
              <tbody>
                {history.map((h, i) => (
                  <tr key={`${h.at}-${i}`}>
                    <td><strong className="rec-when">{fmtDateTime(h.at)}</strong><span className="cell-sub">{ago(h.at)}</span></td>
                    <td>
                      <span className="rec-who">
                        <span className={`rec-avatar role-${h.role}`} aria-hidden="true">{initials(h.user)}</span>
                        <span><strong>{h.user || "—"}</strong><span className={`rec-role role-${h.role}`}>{ROLE[h.role] || h.role}</span></span>
                      </span>
                    </td>
                    <td>{h.feedback_id ? <span className="rec-rec">▶ Recording #{h.feedback_id}</span> : "—"}</td>
                    <td><span className={`status-chip ${ok(h) ? "is-ok" : "is-warn"}`}>{ok(h) ? "✓ " : "✕ "}{OUTCOME[h.outcome] || h.outcome}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

/* ---------------- Consumer report ---------------- */

function ReportTab({ product, rounds, round, ReportView }) {
  const [state, reload] = useData(() => getRoundReport(product.id, round), [product.id, round]);
  if (!rounds.length) {
    return <Empty title="No survey yet">The report appears after the first survey's responses are analyzed.</Empty>;
  }
  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  const { round: r, report, updating, pending } = state.data;
  if (!report) {
    return (
      <Empty title={r.done ? `Survey ${r.number} report is being prepared` : `Survey ${r.number} has just started`}>
        {r.done
          ? `${plural(r.done, "response")} received; the report builds itself as soon as they are analyzed${pending ? ` (${pending} in analysis)` : ""}.`
          : `The report builds itself as soon as the first responses are analyzed (0 of ${r.target} so far).`}
      </Empty>
    );
  }
  const st = report.stats || {};
  const mix = st.sentiment_distribution || {};
  const total = (mix.positive || 0) + (mix.mixed || 0) + (mix.negative || 0) + (mix.neutral || 0);
  const width = (n) => `${total ? (100 * n) / total : 0}%`;
  return (
    <>
      <div className="home-stats org-report-stats">
        <div className="h-stat kpi-card tone-brand">
          <KpiIcon name="responses" />
          <span className="h-stat-label">Consumers (Survey {r.number})</span>
          <span className="h-stat-value">{report.response_count}</span>
          <span className="h-stat-note">{r.status === "live" ? `of ${r.target} · collecting` : "completed"}</span>
        </div>
        <div className="h-stat kpi-card tone-act">
          <KpiIcon name="rating" />
          <span className="h-stat-label">Average rating</span>
          <span className="h-stat-value h-stat-rating">{one(report.overall_rating)} <Stars value={report.overall_rating} /></span>
          <span className="h-stat-note">out of 5</span>
        </div>
        <div className="h-stat h-stat-wide kpi-card tone-teal">
          <KpiIcon name="sentiment" />
          <span className="h-stat-label">Consumer sentiment</span>
          <div className="tone-bar" aria-hidden="true">
            <span className="mix-positive" style={{ width: width(mix.positive || 0) }} />
            <span className="mix-mixed" style={{ width: width(mix.mixed || 0) }} />
            <span className="mix-negative" style={{ width: width(mix.negative || 0) }} />
          </div>
          <div className="bar-legend">
            <span><i className="dot dot-positive" />{mix.positive || 0} positive</span>
            <span><i className="dot dot-mixed" />{mix.mixed || 0} mixed</span>
            <span><i className="dot dot-negative" />{mix.negative || 0} negative</span>
          </div>
        </div>
      </div>
      <div className="report-bar">
        <div>
          <strong>
            Survey {r.number} report
            {(updating || r.status === "live") && <span className="status-chip is-act report-updating-chip">Updating with the latest responses…</span>}
          </strong>
          <span>
            Recipe v{r.recipe_version ?? "—"} · {r.done} of {r.target} consumers{r.status === "live" ? " so far" : ""} · updated {ago(report.generated_at)}.
            {r.status === "live" ? " It updates by itself as new responses are analyzed." : ""}
          </span>
        </div>
      </div>
      <ReportView report={report} product={{ ...product, name: `${product.name} · Survey ${r.number}` }} forClient />
    </>
  );
}

/* ---------------- Recipe ---------------- */

/* Recipe standards of ONE product (its own tab): every result, and the rules made for
   this product (add, edit, remove). Rules from before per-product standards (a whole
   category or all products) still apply: they're listed so they can be removed. */
function StandardsSection({ product, onChanged }) {
  const [state, setState] = useState({ loading: true, error: "", data: null });
  const load = useCallback(() => {
    getRecipe(product.id)
      .then((data) => setState({ loading: false, error: "", data }))
      .catch((err) => setState({ loading: false, error: orgError(err, "The standards couldn't be loaded."), data: null }));
  }, [product.id]);
  useEffect(() => { load(); }, [load]);
  if (state.loading) return <Loading />;
  if (state.error) return <p className="form-error" role="alert">{state.error}</p>;
  const standards = state.data?.standards || { passed: null, failed_count: 0, checked: 0, results: [] };
  const inherited = (standards.results || []).filter((r) => r.scope && r.scope !== "product");
  const changed = () => { load(); onChanged?.(); };
  const removeInherited = async (r) => {
    if (!window.confirm(`Remove “${r.name}”? It applies to ${r.scope === "all" ? "every product" : "every product in this category"}.`)) return;
    await removeStandard(r.rule_id).catch(() => {});
    changed();
  };
  return (
    <div className="pc">
      <StandardsCard standards={standards} productId={product.id} readOnly={false} onChanged={changed} />
      {inherited.length > 0 && (
        <section className="recipe-card" aria-label="Rules from the category or all products">
          <header className="recipe-card-head">
            <div>
              <h3>Also applied: rules for the whole category or all products</h3>
              <p className="muted">Created before standards were set per product. Remove them here if they shouldn't apply to {product.name}.</p>
            </div>
          </header>
          <ul className="std-results">
            {inherited.map((r) => (
              <li key={r.rule_id}>
                <span><strong>{r.name}</strong><span className="muted"> · {r.scope === "all" ? "All products" : "This category"} · {r.expected}</span></span>
                <button type="button" className="link-btn danger-link" onClick={() => removeInherited(r)}>Remove</button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

// The next step (project creation / Open project) is in the setup bar and the page header,
// so the recipe needs no separate "start it in its project" box.
function RecipeSection({ product, onNotice, onSaved }) {
  return (
    <div className="org-recipe">
      <section className="panel product-panel">
        <RecipeTab product={product} onNotice={onNotice} onSaved={onSaved} />
      </section>
    </div>
  );
}

/* ---------------- Survey questions ---------------- */

// The organization manages the questions survey persons ask (the admin dashboard
// only shows them). Saved to the database straight away; the next survey uses them.
function QuestionsSection({ product, onNotice }) {
  return (
    <div className="org-questions">
      <section className="panel product-panel">
        <p className="muted org-recipe-note">
          Survey persons ask these questions, in this order, before recording the consumer. Changes apply to new responses
          straight away; answers already collected are kept. Keep the questions the same between surveys you want to compare.
        </p>
        <QuestionsTab product={product} onNotice={onNotice} />
      </section>
    </div>
  );
}

/* ---------------- Rounds ---------------- */

function starter(r) {
  return r.started_by_role === "client" ? "you" : r.started_by_name;
}

function RoundsSection({ product, detail, onStart, onChanged, onNotice, base = `products/${product.id}` }) {
  const [busy, setBusy] = useState(false);
  const rounds = [...detail.rounds].reverse();
  const live = detail.live_round;
  const next = (detail.rounds[detail.rounds.length - 1]?.number || 0) + 1;

  const end = async (r) => {
    const ok = await confirmDialog({
      title: `End Survey ${r.number} now?`,
      message: `${r.done} of ${r.target} consumers have been surveyed. The report of Survey ${r.number} keeps these responses, and you can then start the next survey.`,
      confirmLabel: `End Survey ${r.number}`,
      cancelLabel: "Keep collecting",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await endRound(product.id, r.number);
      onNotice({ tone: "ok", text: `Survey ${r.number} has ended. The research team has been told.` });
      onChanged();
    } catch (err) {
      onNotice({ tone: "error", text: orgError(err, "The survey couldn't be ended.") });
    } finally {
      setBusy(false);
    }
  };

  // A survey started by mistake: the latest one, while nobody has answered it.
  const remove = async (r) => {
    const ok = await confirmDialog({
      title: `Delete Survey ${r.number}?`,
      message: `Survey ${r.number} has no responses yet. It is removed with its action plans and team assignments; the recipe and its versions stay. The next survey you start is Survey ${r.number} again.`,
      confirmLabel: `Delete Survey ${r.number}`,
      cancelLabel: "Keep it",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await deleteRound(product.id, r.number);
      onNotice({ tone: "ok", text: `Survey ${r.number} was deleted.` });
      onChanged();
    } catch (err) {
      onNotice({ tone: "error", text: orgError(err, "The survey couldn't be deleted.") });
    } finally {
      setBusy(false);
    }
  };
  const latestNumber = detail.rounds[detail.rounds.length - 1]?.number;

  if (!rounds.length) {
    return (
      <Empty
        title="No surveys yet"
        action={<button type="button" className="btn btn-primary" onClick={onStart}>Open project</button>}
      >
        {detail.latest_version ? "Surveys start from the product's project: add its survey plan there, then start the survey." : "Add the recipe first, then start the survey from the product's project."}
      </Empty>
    );
  }
  return (
    <section className="panel org-panel org-rounds">
      <header className="runs-head">
        <div>
          <h2 className="runs-title"><SectionIcon name="history" />Survey runs</h2>
          <p className="muted small">Every survey of {product.name}, newest first: its recipe, progress and result.</p>
        </div>
      </header>
      <ol className="org-timeline">
        {rounds.map((r) => (
          <li key={r.number} className={`org-tl run-card is-${r.status === "live" ? "live" : r.closed_reason === "ended" ? "ended" : "done"}`}>
            <span className={`org-tl-dot${r.status === "live" ? " is-live" : ""}`} aria-hidden="true">{r.number}</span>
            <div className="org-tl-body">
              <div className="org-tl-head">
                <strong>Survey {r.number}</strong>
                <span className={`round-chip${r.status === "live" ? " is-live" : ""}`}>
                  {r.status === "live" ? "collecting" : r.closed_reason === "ended" ? "ended early" : "completed"}
                </span>
                <span className="status-chip is-neutral">Recipe v{r.recipe_version ?? "—"}</span>
                {r.project && (
                  <button type="button" className="status-chip is-neutral chip-link" onClick={() => go(`projects/${r.project.id}`)}
                    title={r.project.name}>{r.project.code}</button>
                )}
              </div>
              <p className="muted small">
                {fmtDate(r.started_at)} – {r.closed_at ? fmtDate(r.closed_at) : "now"}
                {r.due_date ? ` · wanted by ${fmtDate(r.due_date)}` : ""} · started by {starter(r)}
              </p>
              <div className="run-stats">
                <div><span>Submitted</span><strong>{r.done}</strong></div>
                <div><span>Target</span><strong>{r.target}</strong></div>
                <div><span>Remaining</span><strong>{Math.max((r.target || 0) - (r.done || 0), 0)}</strong></div>
                <div className="is-rating"><span>Rating</span><strong>{r.rating != null ? `${one(r.rating)}★` : "—"}</strong></div>
                <div className="run-progress">
                  <span>{r.target ? Math.round((100 * (r.done || 0)) / r.target) : 0}% of target</span>
                  <Progress done={r.done} target={r.target} />
                </div>
              </div>
              {r.note && <blockquote className="run-note">{r.note}</blockquote>}
              <div className="org-tl-actions">
                {r.rating != null && (
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => go(`${base}/report/${r.number}`)}>Open report</button>
                )}
                {r.status === "live" && (
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => end(r)} disabled={busy}>End survey</button>
                )}
                {r.number === latestNumber && !r.done && (
                  <button type="button" className="btn btn-secondary btn-sm acct-danger" onClick={() => remove(r)} disabled={busy}
                    title="Started by mistake? A survey can be deleted while it has no responses.">Delete</button>
                )}
              </div>
            </div>
          </li>
        ))}
        <li className="org-tl">
          <span className="org-tl-dot is-next" aria-hidden="true">+</span>
          <div className="org-tl-body">
            {live ? (
              <p className="muted small">The next survey can start when Survey {live.number} is complete.</p>
            ) : detail.can_start_round ? (
              <button type="button" className="btn btn-secondary btn-sm" onClick={onStart}>Open the project to survey again</button>
            ) : (
              <p className="muted small">Add the recipe first, then start Survey {next}.</p>
            )}
          </div>
        </li>
      </ol>
    </section>
  );
}

/* Recipe standards that stop a round from starting (requirements: no round while any
   applicable standard is NOT MET; the server checks again on every start, no override). */
function StandardsBlocked({ failures, version, onRecipe }) {
  if (!failures?.length) return null;
  return (
    <div className="round-blocked" role="alert">
      <p>
        <strong>A survey can't start: {failures.length} recipe standard{failures.length === 1 ? " is" : "s are"} not met
          {version ? ` by recipe v${version}` : ""}.</strong>{" "}
        Change the recipe (or the standard) until every standard is met.
        {onRecipe && <> <button type="button" className="link-btn" onClick={onRecipe}>Open Recipe standards</button></>}
      </p>
      <div className="table-wrap">
        <table className="data-table std-table">
          <thead>
            <tr><th scope="col">Standard</th><th scope="col">Ingredient</th><th scope="col">Expected</th><th scope="col">Actual</th><th scope="col">Reason</th></tr>
          </thead>
          <tbody>
            {failures.map((f) => (
              <tr key={f.rule_id}>
                <td><strong>{f.name}</strong></td>
                <td>{f.ingredient || "Whole recipe"}</td>
                <td>{f.expected}</td>
                <td>{f.actual}</td>
                <td className="std-reason">{f.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StartSurveyDialog({ project, plan, onClose, onStarted }) {
  const number = plan.next_number || (plan.round?.number || 0) + 1; // numbered across the product's recipes
  const [form, setForm] = useState({ due_date: "", note: "" });
  const [error, setError] = useState("");
  const [blocked, setBlocked] = useState({ failures: [], version: null });
  const [busy, setBusy] = useState(false);
  const today = new Date().toISOString().slice(0, 10);
  const submit = async () => {
    setBusy(true);
    setError("");
    setBlocked({ failures: [], version: null });
    try {
      const res = await startProjectSurvey(project.id, { due_date: form.due_date || null, note: form.note.trim() || null });
      onStarted(res.started);
    } catch (err) {
      const failures = standardsFailures(err);
      if (failures.length) setBlocked({ failures, version: err.response.data.detail.recipe_version });
      else setError(orgError(err, "The survey couldn't be started."));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Start survey · ${project.project_code}`}
      subtitle={`${project.product.name} · Recipe v${project.product_version} · ${plan.target}${plan.total_target ? ` of ${plan.total_target}` : ""} consumers from ${plural(plan.plans.length, "survey plan")}.`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={busy}>{busy ? "Starting…" : "Start survey"}</button>
        </>
      }
    >
      <ul className="start-plans">
        {plan.plans.map((p) => (
          <li key={p.id}><strong>{p.name}</strong> · {p.target} consumers{p.cities.length ? ` · ${p.cities.join(", ")}` : ""}</li>
        ))}
      </ul>
      <label className="field">
        <span className="field-label">Wanted by (optional)</span>
        <DateField ariaLabel="Wanted by" value={form.due_date} min={today} onChange={(d) => setForm({ ...form, due_date: d })} placeholder="No date" />
      </label>
      <label className="field">
        <span className="field-label">Note for the research team (optional)</span>
        <textarea className="input" rows={3} maxLength={1000} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })}
          placeholder="e.g. First test with consumers." />
      </label>
      <p className="org-dialog-info">
        After you start: the research team is notified, the Admin allocates each plan's team, and the Survey {number} report
        builds itself as responses come in.
      </p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <StandardsBlocked failures={blocked.failures} version={blocked.version} />
    </Modal>
  );
}

/* ---------------- Compare rounds ---------------- */

function Delta({ a, b, unit = "", better = "up" }) {
  if (a == null || b == null) return <span className="muted">—</span>;
  const d = Math.round((b - a) * 10) / 10;
  if (d === 0) return <span className="muted">0</span>;
  const good = better === "up" ? d > 0 : d < 0;
  return <span className={good ? "delta-up" : "delta-down"}>{d > 0 ? "+" : ""}{d}{unit}</span>;
}

const pct = (v) => (v == null ? "—" : `${v}%`);

// Also used by the admin dashboard's product report ("Compare rounds").
export function CompareSection({ product, rounds, base = `products/${product.id}` }) {
  const numbers = rounds.map((r) => r.number);
  // Surveys are compared as separate surveys (Survey 1 and Survey 2 may test the same recipe);
  // a survey is never compared with itself. Two chosen: the detailed comparison; more: side by side.
  const [picked, setPicked] = useState(() => numbers.slice(-2));
  const pair = [picked[0], picked[1]];
  const many = picked.length > 2 ? picked : null;
  const [state, reload] = useData(() => (picked.length < 2 ? Promise.resolve(null) : compareRounds(product.id, pair[0], pair[1], many)),
    [product.id, picked.join(",")]);

  if (rounds.length < 2) {
    return (
      <Empty title="Comparison needs two surveys"
        action={<button type="button" className="btn btn-primary" onClick={() => go(`${base}/recipe`)}>Go to recipe</button>}>
        Change the recipe and start a new survey; then you can see whether the change worked.
      </Empty>
    );
  }
  const toggle = (n) => setPicked((p) => (p.includes(n) ? p.filter((x) => x !== n) : [...p, n].sort((x, y) => x - y)));
  const picker = (
    <div className="org-compare-pick compare-chips" role="group" aria-label="Surveys to compare">
      <strong>Compare surveys</strong>
      {rounds.map((r) => (
        <button key={r.number} type="button" className={`compare-chip${picked.includes(r.number) ? " is-on" : ""}`}
          aria-pressed={picked.includes(r.number)} onClick={() => toggle(r.number)}>
          Survey {r.number}
          <span className="muted small"> · Recipe v{r.recipe_version ?? "—"}{r.project?.code ? ` · ${r.project.code}` : ""}</span>
        </button>
      ))}
      <span className="muted small">{picked.length < 2 ? "Choose at least two different surveys." : `${picked.length} chosen`}</span>
    </div>
  );
  if (picked.length < 2) return picker;
  if (state.loading && !state.data) return <>{picker}<Loading /></>;
  if (state.error) return <>{picker}<ErrorPanel text={state.error} onRetry={reload} /></>;
  const c = state.data;
  if (!c) return picker;
  if (many && c.surveys) {
    const rows = [
      ["Consumers (submitted responses)", (s) => s.numbers.consumers],
      ["Average rating", (s) => one(s.numbers.rating)],
      ["Positive consumers", (s) => pct(s.numbers.positive_pct)],
      ["Negative consumers", (s) => pct(s.numbers.negative_pct)],
      ["Would buy", (s) => pct(s.numbers.would_buy_pct)],
      ["Prefer this product", (s) => pct(s.numbers.preferred_pct)],
    ];
    return (
      <>
        {picker}
        <section className="panel org-panel">
          <h3 className="org-panel-title">Surveys side by side</h3>
          <div className="table-wrap">
            <table className="data-table org-num-table">
              <thead>
                <tr><th scope="col" />{c.surveys.map((s) => (
                  <th key={s.number} scope="col">Survey {s.number}<br /><span className="muted small">Recipe v{s.recipe_version ?? "—"} · {s.status === "live" ? "collecting" : "completed"}</span></th>
                ))}</tr>
              </thead>
              <tbody>
                {rows.map(([label, get]) => (
                  <tr key={label}><td>{label}</td>{c.surveys.map((s) => <td key={s.number}>{s.has_report ? get(s) : "—"}</td>)}</tr>
                ))}
                <tr><td>Top concerns</td>{c.surveys.map((s) => (
                  <td key={s.number}>{(s.aspects || []).filter((x) => x.concern_pct).slice(0, 3).map((x) => `${x.name} ${x.concern_pct}%`).join(", ") || "—"}</td>
                ))}</tr>
              </tbody>
            </table>
          </div>
          <p className="field-hint">Each column is one survey's own responses; surveys of the same recipe are never merged.</p>
        </section>
      </>
    );
  }
  const { a, b } = c;
  const na = a.numbers;
  const nb = b.numbers;
  return (
    <>
      {picker}
      {!a.has_report || !b.has_report ? (
        <Empty title={`Survey ${!b.has_report ? b.number : a.number} has no analyzed responses yet`}>
          The comparison fills in as soon as the first responses of that survey are analyzed.
        </Empty>
      ) : (
        <>
          <div className="org-hero">
            <div>
              <p className="eyebrow">Result</p>
              <h3 className="org-hero-title">{c.headline || `Survey ${a.number} compared with Survey ${b.number}.`}</h3>
            </div>
            <button type="button" className="btn btn-secondary" onClick={() => downloadComparisonPdf({ product, comparison: c })}>
              <Icon name="download" /> Comparison PDF
            </button>
          </div>
          <div className="org-compare-grid">
            <section className="panel org-panel rc-tone cmp-panel cmp-numbers">
              <h3 className="org-panel-title cmp-title"><SectionIcon name="compare" />Key numbers</h3>
              <div className="table-wrap">
                <table className="data-table org-num-table">
                  <thead>
                    <tr><th scope="col" /><th scope="col">Survey {a.number}</th><th scope="col">Survey {b.number}</th><th scope="col">Change</th></tr>
                  </thead>
                  <tbody>
                    <tr><td>Consumers</td><td>{na.consumers}</td><td>{nb.consumers}</td><td className="muted">{b.status === "live" ? "so far" : ""}</td></tr>
                    <tr><td>Average rating</td><td>{one(na.rating)}</td><td>{one(nb.rating)}</td><td><Delta a={na.rating} b={nb.rating} /></td></tr>
                    <tr><td>Positive consumers</td><td>{pct(na.positive_pct)}</td><td>{pct(nb.positive_pct)}</td><td><Delta a={na.positive_pct} b={nb.positive_pct} unit=" pts" /></td></tr>
                    {(na.would_buy_pct != null || nb.would_buy_pct != null) && (
                      <tr><td>Would buy</td><td>{pct(na.would_buy_pct)}</td><td>{pct(nb.would_buy_pct)}</td><td><Delta a={na.would_buy_pct} b={nb.would_buy_pct} unit=" pts" /></td></tr>
                    )}
                    {(na.preferred_pct != null || nb.preferred_pct != null) && (
                      <tr><td>Prefer this product</td><td>{pct(na.preferred_pct)}</td><td>{pct(nb.preferred_pct)}</td><td><Delta a={na.preferred_pct} b={nb.preferred_pct} unit=" pts" /></td></tr>
                    )}
                    {(na.ideal_price || nb.ideal_price) && (
                      <tr><td>Ideal price</td><td>{na.ideal_price || "—"}</td><td>{nb.ideal_price || "—"}</td><td /></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="panel org-panel rc-tone cmp-panel cmp-said">
              <h3 className="org-panel-title cmp-title"><SectionIcon name="people" />What consumers said</h3>
              <p className="muted small cmp-legend"><i className="cmp-swatch is-a" /> Concern in Survey {a.number} <i className="cmp-swatch is-b" /> Concern in Survey {b.number} · lower is better</p>
              {c.aspects.length === 0 ? (
                <p className="muted">No aspects to compare yet.</p>
              ) : (
                <div className="table-wrap">
                  <table className="data-table org-num-table">
                    <thead>
                      <tr><th scope="col">Aspect</th><th scope="col">Concern R{a.number}</th><th scope="col">Concern R{b.number}</th><th scope="col">Praise R{b.number}</th></tr>
                    </thead>
                    <tbody>
                      {c.aspects.map((r) => (
                        <tr key={r.name}>
                          <td>
                            <strong>{r.name}</strong>
                            <span className="cmp-bars" aria-hidden="true">
                              <i className="is-a" style={{ width: `${r.a.concern_pct || 0}%` }} />
                              <i className="is-b" style={{ width: `${r.b.concern_pct || 0}%` }} />
                            </span>
                          </td>
                          <td>{pct(r.a.concern_pct)}</td>
                          <td>
                            {pct(r.b.concern_pct)} <small><Delta a={r.a.concern_pct} b={r.b.concern_pct} better="down" /></small>
                          </td>
                          <td>{pct(r.b.praise_pct)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <section className="panel org-panel org-span rc-tone cmp-panel cmp-recipe">
              <h3 className="org-panel-title cmp-title">
                <SectionIcon name="ingredients" />Recipe used
                <span className="cmp-versions"><span>v{a.recipe_version ?? "—"}</span>→<span>v{b.recipe_version ?? "—"}</span></span>
              </h3>
              {c.recipe_changes.length ? (
                <ul className="cmp-changes">
                  {c.recipe_changes.map((x) => {
                    const kind = /^added/i.test(x) ? "is-add" : /^removed/i.test(x) ? "is-remove" : x.includes("→") ? "is-change" : "is-note";
                    const tag = { "is-add": "Added", "is-remove": "Removed", "is-change": "Changed", "is-note": "Updated" }[kind];
                    return <li key={x} className={kind}><span className="cmp-tag">{tag}</span><span>{x.replace(/^(Added|Removed)\s*/i, "")}</span></li>;
                  })}
                </ul>
              ) : (
                <p className="muted">{c.same_recipe ? "Same recipe in both surveys." : "No recipe details to compare."}</p>
              )}
            </section>
          </div>
        </>
      )}
    </>
  );
}

/* ================================================================
   Updates
   ================================================================ */

const UPDATE_FILTERS = [
  ["all", "All"],
  ["recipe", "Recipe changes"],
  ["round_started", "Surveys started"],
  ["round_completed", "Reports"],
];

function UpdatesPage({ onSeen }) {
  const [state, reload] = useData(() => getUpdates(200), []);
  const [kind, setKind] = useState("all");
  useEffect(() => {
    markUpdatesSeen().then(onSeen).catch(() => {});
  }, [onSeen]);
  const all = state.data?.updates || [];
  const count = (k) => all.filter((u) => u.kind === k).length;
  const shown = kind === "all" ? all : all.filter((u) => u.kind === kind);
  return (
    <section aria-labelledby="org-updates-page">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="org-updates-page">Updates</h1>
          <p className="home-sub">Everything that happened on your products, newest first.</p>
        </div>
      </header>
      {state.loading && !state.data ? (
        <Loading />
      ) : state.error ? (
        <ErrorPanel text={state.error} onRetry={reload} />
      ) : (
        <>
          <section className="panel tl-panel" aria-label="All updates">
            <div className="chip-filters" role="tablist" aria-label="Show">
              {UPDATE_FILTERS.map(([k, label]) => (
                <button key={k} type="button" role="tab" aria-selected={kind === k} className={`chip-filter${kind === k ? " is-on" : ""}`}
                  onClick={() => setKind(k)}>
                  {label}<span className="chip-count">{k === "all" ? all.length : count(k)}</span>
                </button>
              ))}
            </div>
            <UpdatesList items={shown} grouped compact />
          </section>
        </>
      )}
    </section>
  );
}

/* ================================================================
   Account
   ================================================================ */

function AccountPage() {
  const [state, reload] = useData(getOrgAccount, []);
  // Password: simple mode (no real emails yet, never in production): new password + repeat.
  // Email mode: 1. email a code (no current password needed), 2. code + new password + repeat.
  const [simple, setSimple] = useState(false);
  const [step, setStep] = useState(1);
  useEffect(() => {
    let alive = true;
    getSetupStatus(API_BASE_URL)
      .then((s) => alive && s?.password_reset === "simple" && (setSimple(true), setStep(2)))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  const [form, setForm] = useState({ code: "", next: "", repeat: "" });
  const [show, setShow] = useState(false);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: k === "code" ? e.target.value.replace(/\D/g, "") : e.target.value });

  const sendCode = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const res = await sendMyPasswordCode(API_BASE_URL);
      setStep(2);
      setMsg({ tone: res.sent ? "info" : "warn", text: res.detail });
    } catch (err) {
      setMsg({ tone: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  };

  const change = async (e) => {
    e.preventDefault();
    const problem = !simple && !/^\d{6}$/.test(form.code)
      ? "Enter the 6-digit code from the email."
      : passwordProblem(form.next) || (form.next !== form.repeat ? "The two new passwords are different." : "");
    if (problem) {
      setMsg({ tone: "error", text: problem });
      return;
    }
    setBusy(true);
    try {
      if (simple) await changePasswordSimple(API_BASE_URL, form.next);
      else await changePasswordWithCode(API_BASE_URL, form.code, form.next);
      setForm({ code: "", next: "", repeat: "" });
      setStep(simple ? 2 : 1);
      setMsg({ tone: "ok", text: "Password changed. Every other sign-in of this Super Admin account has ended." });
    } catch (err) {
      setMsg({ tone: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  };

  const everywhere = async () => {
    const ok = await confirmDialog({
      title: "Sign out everywhere?",
      message: "Every sign-in of this Super Admin account ends, on every computer, including this one.",
      confirmLabel: "Sign out everywhere",
      danger: true,
    });
    if (ok) await signOutEverywhere(API_BASE_URL);
  };

  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  const a = state.data;
  const email = a.email || a.mobile || "";
  return (
    <section className="acct" aria-labelledby="org-account-title">
      <button type="button" className="back-link" onClick={() => window.history.back()}><Icon name="back" /> Back</button>

      <header className="panel acct-hero">
        <span className="acct-avatar" aria-hidden="true">{(a.name || "?").trim()[0]?.toUpperCase()}</span>
        <div className="acct-id">
          <h1 id="org-account-title">{a.name}</h1>
          <p>
            <span className="status-chip is-act">Super Admin</span>
            <span className="acct-email">{email || "—"}</span>
          </p>
        </div>
        <dl className="acct-facts">
          <div><dt>Products</dt><dd>{a.products}</dd></div>
          <div><dt>Last sign-in</dt><dd>{fmtDateTime(a.last_login_at) || "—"}</dd></div>
          <div><dt>Your contact</dt><dd>{a.contact}</dd></div>
        </dl>
      </header>

      <div className="acct-grid">
        <section className="panel acct-card" aria-labelledby="acct-password">
          <div className="acct-card-head">
            <span className="acct-icon"><Icon name="lock" size={20} /></span>
            <div>
              <h2 id="acct-password">Password</h2>
              <p className="muted">
                {simple ? (
                  <>Choose a new password. Every other sign-in of this account ends; this browser stays signed in.</>
                ) : (
                  <>No need to remember the current one: we email a 6-digit code to <strong>{email}</strong>, then you choose the new password.</>
                )}
              </p>
            </div>
          </div>

          {step === 1 ? (
            <div className="acct-actions">
              <button type="button" className="btn btn-primary" disabled={busy} onClick={sendCode}>
                <Icon name="mail" /> {busy ? "Sending…" : "Email me a code"}
              </button>
            </div>
          ) : (
            <form className="acct-form" onSubmit={change} noValidate>
              {!simple && (
                <label className="field">
                  <span className="field-label">Code from the email</span>
                  <input className="input acct-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
                    placeholder="6 digits" value={form.code} onChange={set("code")} autoFocus />
                </label>
              )}
              <div className="field-row">
                <label className="field">
                  <span className="field-label">New password</span>
                  <input className="input" type={show ? "text" : "password"} autoComplete="new-password"
                    placeholder="8+ letters and numbers" value={form.next} onChange={set("next")} />
                </label>
                <label className="field">
                  <span className="field-label">Repeat new password</span>
                  <input className="input" type={show ? "text" : "password"} autoComplete="new-password" value={form.repeat} onChange={set("repeat")} />
                </label>
              </div>
              <label className="acct-show">
                <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} /> Show passwords
              </label>
              <div className="acct-actions">
                <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : "Save new password"}</button>
                {!simple && (
                  <>
                    <button type="button" className="btn btn-secondary" disabled={busy} onClick={sendCode}>Send a new code</button>
                    <button type="button" className="link-btn" disabled={busy} onClick={() => { setStep(1); setMsg(null); }}>Cancel</button>
                  </>
                )}
              </div>
            </form>
          )}
          {msg && (
            <p className={`acct-msg is-${msg.tone}`} role={msg.tone === "error" ? "alert" : "status"}>{msg.text}</p>
          )}
        </section>

        <section className="panel acct-card" aria-labelledby="acct-signins">
          <div className="acct-card-head">
            <span className="acct-icon is-neg"><Icon name="logout" size={20} /></span>
            <div>
              <h2 id="acct-signins">Sign-ins</h2>
              <p className="muted">Lost a laptop, or someone left the company? End every sign-in of this account at once, including this one.</p>
            </div>
          </div>
          <div className="acct-actions">
            <button type="button" className="btn btn-secondary acct-danger" onClick={everywhere}>Sign out everywhere</button>
          </div>
          <p className="acct-note muted">To change the name or the sign-in email, contact {a.contact}.</p>
        </section>
      </div>
      <SuperAdminsCard />
    </section>
  );
}