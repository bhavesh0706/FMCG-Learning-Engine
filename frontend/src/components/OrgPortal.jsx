import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL } from "../config";
import { changePassword, signOut, signOutEverywhere } from "../services/auth";
import {
  compareRounds,
  endRound,
  getOrgAccount,
  getOrgProduct,
  getOrgProducts,
  getOverview,
  getRoundReport,
  getUnreadUpdates,
  getUpdates,
  markUpdatesSeen,
  orgError,
  startRound,
} from "../services/orgApi";
import { downloadComparisonPdf } from "../services/comparisonPdf";
import CatalogPage, { QuestionsTab, RecipeTab } from "./CatalogPage";
import { ConfirmHost, confirmDialog } from "./ConfirmDialog";
import Modal from "./Modal";

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
  if (round.status === "live") return <span className="round-chip is-live">Round {round.number} · collecting</span>;
  if (round.closed_reason === "ended") return <span className="round-chip">Round {round.number} · ended</span>;
  return <span className="round-chip">Round {round.number} · {round.rating != null ? "report ready" : "completed"}</span>;
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
const UPDATE_TONE = { recipe: "is-act", round_started: "is-brand", round_completed: "is-ok" };

function updateLink(u) {
  if (u.tab === "report" && u.round) return `products/${u.product_id}/report/${u.round}`;
  return `products/${u.product_id}/${u.tab || "report"}`;
}

function UpdatesList({ items, showOpen = false }) {
  if (!items.length) return <p className="muted org-feed-empty">Nothing has happened yet. Updates appear here as rounds run and recipes change.</p>;
  return (
    <ul className="org-feed">
      {items.map((u, i) => (
        <li key={`${u.kind}-${u.product_id}-${u.at}-${i}`}>
          <button type="button" className={`org-feed-item${u.unread ? " is-unread" : ""}`} onClick={() => go(updateLink(u))}>
            <span className={`org-feed-icon ${UPDATE_TONE[u.kind] || ""}`}><Icon name={UPDATE_ICON[u.kind] || "check"} size={20} /></span>
            <span className="org-feed-text">
              <strong>{u.title}</strong>
              <span className="muted">
                {[u.text, ago(u.at)].filter(Boolean).join(" · ")}
                {showOpen && <span className="org-feed-open"> · Open</span>}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
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
    if (!window.location.hash || !["overview", "products", "catalog", "updates", "account"].includes(parts[0])) {
      window.history.replaceState(null, "", "#/overview");
    }
    document.title = `${user?.name || "Organization"} · Consumer insights`;
  }, [parts, user]);

  let body;
  if (page === "products" && parts[1]) {
    body = <ProductPage key={parts[1]} productId={Number(parts[1])} tab={parts[2] || "report"} round={parts[3] ? Number(parts[3]) : null} ReportView={ReportView} />;
  } else if (page === "products") body = <ProductsPage />;
  // Products and categories: the same Catalog page as the admin dashboard, with the
  // organization's add / change / deactivate / delete (each change notifies the admins).
  else if (page === "catalog") body = <CatalogPage mode="organization" />;
  else if (page === "updates") body = <UpdatesPage onSeen={() => setUnread(0)} />;
  else if (page === "account") body = <AccountPage />;
  else body = <OverviewPage user={user} />;

  const nav = [
    ["overview", "Overview"],
    ["products", "Products"],
    ["catalog", "Catalog"],
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
              {nav.map(([key, label]) => (
                <button key={key} type="button" className={page === key ? "is-active" : ""} aria-current={page === key ? "page" : undefined}
                  onClick={() => go(key)}>
                  {label}
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
        aria-label={`${user?.name}, organization. Account menu`}>
        <span className="user-avatar" aria-hidden="true">{initial}</span>
        <span className="user-chip-text">
          <strong>{user?.name}</strong>
          <span>Organization</span>
        </span>
      </button>
      {open && (
        <div className="user-pop" role="menu">
          <div className="user-pop-head">
            <span className="user-avatar user-avatar-lg" aria-hidden="true">{initial}</span>
            <span>
              <strong>{user?.name}</strong>
              <span>{user?.email || user?.mobile}</span>
              <span className="status-chip is-act user-role">Organization</span>
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
  return (
    <span className="org-rating-change">
      {one(product.previous_rating)} → <strong className={up ? "delta-up" : "delta-down"}>{one(r.rating)}</strong>
    </span>
  );
}

function OverviewPage() {
  const [state, reload] = useData(getOverview, []);
  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  const { numbers, products, updates } = state.data;
  return (
    <section aria-labelledby="org-overview-title">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="org-overview-title">{greeting()}</h1>
          <p className="home-sub">Here's where your products stand with consumers.</p>
        </div>
      </header>

      <div className="home-stats org-kpis">
        <div className="h-stat">
          <span className="h-stat-label">Products</span>
          <span className="h-stat-value">{numbers.products}</span>
          <span className="h-stat-note">{numbers.surveyed} surveyed so far</span>
        </div>
        <div className="h-stat">
          <span className="h-stat-label">Consumer responses</span>
          <span className="h-stat-value">{numbers.responses}</span>
          <span className="h-stat-note">all rounds</span>
        </div>
        <div className="h-stat">
          <span className="h-stat-label">Open rounds</span>
          <span className="h-stat-value">{numbers.open_rounds}</span>
          <span className="h-stat-note">collecting surveys now</span>
        </div>
      </div>

      <div className="org-two">
        <section className="panel org-panel" aria-labelledby="org-products-title">
          <div className="org-panel-head">
            <h2 id="org-products-title">Your products</h2>
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
                    <th scope="col">Current round</th>
                    <th scope="col" className="col-category">Progress</th>
                    <th scope="col">Rating</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((p) => (
                    <tr key={p.id} className="is-clickable" onClick={() => go(`products/${p.id}/report`)}>
                      <td>
                        <div className="cell-product">
                          <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); go(`products/${p.id}/report`); }}>{p.name}</button>
                          <span>{p.category_name}</span>
                        </div>
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

        <section className="panel org-panel" aria-labelledby="org-updates-title">
          <div className="org-panel-head">
            <h2 id="org-updates-title">Latest updates</h2>
            <button type="button" className="link-btn" onClick={() => go("updates")}>See all <Icon name="chevron" /></button>
          </div>
          <UpdatesList items={updates} />
        </section>
      </div>
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
          <p className="home-sub">{plural(products.length, "product")}. Open one to read its report, change its recipe or start a new round.</p>
        </div>
      </header>
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
              <button key={p.id} type="button" className="panel org-card" onClick={() => go(`products/${p.id}/report`)}>
                <span className="eyebrow">{p.category_name || "Product"}</span>
                <strong className="org-card-name">{p.name}</strong>
                <RoundChip round={r} />
                {r ? (
                  <>
                    <Progress done={r.done} target={r.target} />
                    <span className="muted small">{r.done} of {r.target} consumers · Recipe v{r.recipe_version ?? "—"}</span>
                  </>
                ) : (
                  <span className="muted small">Waiting for the first survey round</span>
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

const TABS = [
  ["report", "Consumer report"],
  ["recipe", "Recipe"],
  ["questions", "Survey questions"],
  ["rounds", "Rounds"],
  ["compare", "Compare rounds"],
];

function ProductPage({ productId, tab, round, ReportView }) {
  const [state, reload] = useData(() => getOrgProduct(productId), [productId]);
  const [dialog, setDialog] = useState(false);
  const [notice, setNotice] = useState(null);
  const onNotice = useCallback((n) => setNotice(n), []);

  if (state.loading && !state.data) return <Loading />;
  if (state.error) {
    return (
      <section>
        <button type="button" className="back-link" onClick={() => go("products")}><Icon name="back" /> Products</button>
        <ErrorPanel text={state.error} onRetry={reload} />
      </section>
    );
  }
  const detail = state.data;
  const { product, rounds, live_round: live } = detail;
  const startTitle = live ? `Round ${live.number} is still collecting surveys` : !detail.latest_version ? "Add a recipe first" : "";
  const shownRound = tab === "report" ? round || rounds[rounds.length - 1]?.number : null;

  return (
    <section className="org-product" aria-labelledby="org-product-title">
      <button type="button" className="back-link" onClick={() => go("products")}><Icon name="back" /> Products</button>
      <header className="home-head org-product-head">
        <div className="page-heading">
          <p className="eyebrow">{product.category_name}</p>
          <h1 id="org-product-title">{product.name}</h1>
          <p className="home-sub">
            {[product.sku, detail.latest_version ? `Recipe v${detail.latest_version} is the latest` : "No recipe yet"].filter(Boolean).join(" · ")}
          </p>
        </div>
        <div className="org-head-actions">
          {tab === "report" && rounds.length > 1 && (
            <select className="select" value={shownRound || ""} aria-label="Round"
              onChange={(e) => go(`products/${productId}/report/${e.target.value}`)}>
              {[...rounds].reverse().map((r) => (
                <option key={r.number} value={r.number}>Round {r.number} ({r.status === "live" ? "collecting" : "completed"})</option>
              ))}
            </select>
          )}
          <button type="button" className="btn btn-primary" onClick={() => setDialog(true)} disabled={!detail.can_start_round} title={startTitle}>
            Start new round
          </button>
        </div>
      </header>

      <div className="page-tabs-u" role="tablist" aria-label={product.name}>
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "is-on" : ""}
            onClick={() => go(`products/${productId}/${key}`)}>
            {label}
          </button>
        ))}
      </div>

      <Notice notice={notice} onClose={() => setNotice(null)} />

      {tab === "report" && <ReportTab product={product} rounds={rounds} round={shownRound} ReportView={ReportView} />}
      {tab === "recipe" && (
        <RecipeSection product={product} detail={detail} onNotice={onNotice} onSaved={() => reload({ silent: true })} onStart={() => setDialog(true)} />
      )}
      {tab === "questions" && <QuestionsSection product={product} onNotice={onNotice} />}
      {tab === "rounds" && (
        <RoundsSection product={product} detail={detail} onStart={() => setDialog(true)} onChanged={() => reload({ silent: true })} onNotice={onNotice} />
      )}
      {tab === "compare" && <CompareSection product={product} rounds={rounds} />}

      {dialog && (
        <NewRoundDialog
          product={product}
          detail={detail}
          onClose={() => setDialog(false)}
          onStarted={(r) => {
            setDialog(false);
            setNotice({ tone: "ok", text: `Round ${r.number} started. The research team and the field executives have been told.` });
            reload({ silent: true });
            go(`products/${productId}/rounds`);
          }}
        />
      )}
    </section>
  );
}

/* ---------------- Consumer report ---------------- */

function ReportTab({ product, rounds, round, ReportView }) {
  const [state, reload] = useData(() => getRoundReport(product.id, round), [product.id, round]);
  if (!rounds.length) {
    return <Empty title="No survey round yet">The report appears after the first round's surveys are analyzed.</Empty>;
  }
  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  const { round: r, report, updating, pending } = state.data;
  if (!report) {
    return (
      <Empty title={r.done ? `Round ${r.number} report is being prepared` : `Round ${r.number} has just started`}>
        {r.done
          ? `${plural(r.done, "survey")} received; the report builds itself as soon as they are analyzed${pending ? ` (${pending} in analysis)` : ""}.`
          : `The report builds itself as soon as the first surveys are analyzed (0 of ${r.target} so far).`}
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
        <div className="h-stat">
          <span className="h-stat-label">Consumers (Round {r.number})</span>
          <span className="h-stat-value">{report.response_count}</span>
          <span className="h-stat-note">{r.status === "live" ? `of ${r.target} · collecting` : "completed"}</span>
        </div>
        <div className="h-stat">
          <span className="h-stat-label">Average rating</span>
          <span className="h-stat-value h-stat-rating">{one(report.overall_rating)} <Stars value={report.overall_rating} /></span>
          <span className="h-stat-note">out of 5</span>
        </div>
        <div className="h-stat h-stat-wide">
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
            Round {r.number} report
            {(updating || r.status === "live") && <span className="status-chip is-act report-updating-chip">Updating with the latest surveys…</span>}
          </strong>
          <span>
            Recipe v{r.recipe_version ?? "—"} · {r.done} of {r.target} consumers{r.status === "live" ? " so far" : ""} · updated {ago(report.generated_at)}.
            {r.status === "live" ? " It updates by itself as new surveys are analyzed." : ""}
          </span>
        </div>
      </div>
      <ReportView report={report} product={{ ...product, name: `${product.name} · Round ${r.number}` }} forClient />
    </>
  );
}

/* ---------------- Recipe ---------------- */

function RecipeSection({ product, detail, onNotice, onSaved, onStart }) {
  const latest = detail.versions[0];
  const untested = latest && !latest.rounds?.length && !detail.live_round;
  return (
    <div className="org-recipe">
      {untested && (
        <div className="callout org-callout" role="status">
          <span>Recipe <strong>v{latest.version}</strong> hasn't been tested with consumers yet.</span>
          <button type="button" className="btn btn-primary btn-sm" onClick={onStart}>Start new round</button>
        </div>
      )}
      <section className="panel product-panel">
        <p className="muted org-recipe-note">
          The report's recommendations refer to these ingredients and steps. Saving a change creates a new version and tells the
          research team; start a new round to test it with consumers.
        </p>
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
          Survey persons ask these questions, in this order, before recording the consumer. Changes apply to new surveys
          straight away; answers already collected are kept. Keep the questions the same between rounds you want to compare.
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

function RoundsSection({ product, detail, onStart, onChanged, onNotice }) {
  const [busy, setBusy] = useState(false);
  const rounds = [...detail.rounds].reverse();
  const live = detail.live_round;
  const next = (detail.rounds[detail.rounds.length - 1]?.number || 0) + 1;

  const end = async (r) => {
    const ok = await confirmDialog({
      title: `End Round ${r.number} now?`,
      message: `${r.done} of ${r.target} consumers have been surveyed. The report of Round ${r.number} keeps these surveys, and you can then start the next round.`,
      confirmLabel: `End Round ${r.number}`,
      cancelLabel: "Keep collecting",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await endRound(product.id, r.number);
      onNotice({ tone: "ok", text: `Round ${r.number} has ended. The research team has been told.` });
      onChanged();
    } catch (err) {
      onNotice({ tone: "error", text: orgError(err, "The round couldn't be ended.") });
    } finally {
      setBusy(false);
    }
  };

  if (!rounds.length) {
    return (
      <Empty
        title="No rounds yet"
        action={detail.can_start_round ? <button type="button" className="btn btn-primary" onClick={onStart}>Start Round 1</button> : null}
      >
        {detail.latest_version ? "Start the first round to collect consumer feedback." : "Add the recipe first, then start the first round."}
      </Empty>
    );
  }
  return (
    <section className="panel org-panel org-rounds">
      <ol className="org-timeline">
        {rounds.map((r) => (
          <li key={r.number} className="org-tl">
            <span className={`org-tl-dot${r.status === "live" ? " is-live" : ""}`} aria-hidden="true">{r.number}</span>
            <div className="org-tl-body">
              <div className="org-tl-head">
                <strong>Round {r.number}</strong>
                <span className={`round-chip${r.status === "live" ? " is-live" : ""}`}>
                  {r.status === "live" ? "collecting" : r.closed_reason === "ended" ? "ended early" : "completed"}
                </span>
                <span className="status-chip is-neutral">Recipe v{r.recipe_version ?? "—"}</span>
              </div>
              <p className="muted small">
                {fmtDate(r.started_at)} – {r.closed_at ? fmtDate(r.closed_at) : "now"}
                {r.due_date ? ` · wanted by ${fmtDate(r.due_date)}` : ""} · started by {starter(r)}
              </p>
              <Progress done={r.done} target={r.target} />
              <p className="muted small">
                {r.done} of {r.target} consumers{r.rating != null ? ` · rating ${one(r.rating)}★` : ""}
                {r.note ? ` · “${r.note}”` : ""}
              </p>
              <div className="org-tl-actions">
                {r.rating != null && (
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => go(`products/${product.id}/report/${r.number}`)}>Open report</button>
                )}
                {r.status === "live" && (
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => end(r)} disabled={busy}>End round</button>
                )}
              </div>
            </div>
          </li>
        ))}
        <li className="org-tl">
          <span className="org-tl-dot is-next" aria-hidden="true">+</span>
          <div className="org-tl-body">
            {live ? (
              <p className="muted small">The next round can start when Round {live.number} is complete.</p>
            ) : detail.can_start_round ? (
              <button type="button" className="btn btn-secondary btn-sm" onClick={onStart}>Start Round {next}</button>
            ) : (
              <p className="muted small">Add the recipe first, then start Round {next}.</p>
            )}
          </div>
        </li>
      </ol>
    </section>
  );
}

function NewRoundDialog({ product, detail, onClose, onStarted }) {
  const last = detail.rounds[detail.rounds.length - 1];
  const number = (last?.number || 0) + 1;
  const [form, setForm] = useState({
    recipe_version: detail.latest_version || "",
    target: String(last?.target || detail.default_target || 30),
    due_date: "",
    note: "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const today = new Date().toISOString().slice(0, 10);

  const submit = async () => {
    const target = Number(form.target);
    if (!Number.isInteger(target) || target < 1) {
      setError("Enter how many consumers to survey (a whole number).");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await startRound(product.id, {
        recipe_version: Number(form.recipe_version) || null,
        target,
        due_date: form.due_date || null,
        note: form.note.trim() || null,
      });
      onStarted(res.round);
    } catch (err) {
      setError(orgError(err, "The round couldn't be started."));
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`Start Round ${number} of ${product.name}`}
      subtitle={`Consumers will taste the product made with the recipe below.${last ? ` Round ${last.number} stays as it is, so you can compare.` : ""}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={busy}>{busy ? "Starting…" : `Start Round ${number}`}</button>
        </>
      }
    >
      <label className="field">
        <span className="field-label">Recipe for this round</span>
        <select className="select" value={form.recipe_version} onChange={set("recipe_version")}>
          {detail.versions.map((v) => (
            <option key={v.version} value={v.version}>
              v{v.version} · {fmtDate(v.changed_at)}{v.changes?.[0] ? ` · ${v.changes[0].length > 48 ? `${v.changes[0].slice(0, 47)}…` : v.changes[0]}` : ""}
            </option>
          ))}
        </select>
      </label>
      <div className="field-row">
        <label className="field">
          <span className="field-label">Consumers to survey</span>
          <input className="input" type="number" min="1" max="5000" inputMode="numeric" value={form.target} onChange={set("target")} />
        </label>
        <label className="field">
          <span className="field-label">Wanted by (optional)</span>
          <input className="input" type="date" min={today} value={form.due_date} onChange={set("due_date")} />
        </label>
      </div>
      <label className="field">
        <span className="field-label">What changed (for the research team)</span>
        <textarea className="input" rows={3} maxLength={1000} value={form.note} onChange={set("note")}
          placeholder={last ? `e.g. Less sugar, as the Round ${last.number} report suggested.` : "e.g. First test with consumers."} />
      </label>
      <p className="org-dialog-info">
        After you start: the research team is notified, field executives see Round {number} for this product in their app, and the
        Round {number} report builds itself as surveys come in.
      </p>
      {error && <p className="form-error" role="alert">{error}</p>}
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
export function CompareSection({ product, rounds }) {
  const numbers = rounds.map((r) => r.number);
  const [pair, setPair] = useState(() => [numbers[numbers.length - 2], numbers[numbers.length - 1]]);
  const [state, reload] = useData(() => compareRounds(product.id, pair[0], pair[1]), [product.id, pair[0], pair[1]]);

  if (rounds.length < 2) {
    return (
      <Empty title="Comparison needs two rounds"
        action={<button type="button" className="btn btn-primary" onClick={() => go(`products/${product.id}/recipe`)}>Go to recipe</button>}>
        Change the recipe and start a new round; then you can see whether the change worked.
      </Empty>
    );
  }
  const picker = (
    <div className="org-compare-pick">
      <strong>Compare</strong>
      <select className="select" value={pair[0]} aria-label="First round" onChange={(e) => setPair([Number(e.target.value), pair[1]])}>
        {numbers.map((n) => <option key={n} value={n}>Round {n}</option>)}
      </select>
      <span aria-hidden="true">→</span>
      <select className="select" value={pair[1]} aria-label="Second round" onChange={(e) => setPair([pair[0], Number(e.target.value)])}>
        {numbers.map((n) => <option key={n} value={n}>Round {n}</option>)}
      </select>
    </div>
  );
  if (state.loading && !state.data) return <>{picker}<Loading /></>;
  if (state.error) return <>{picker}<ErrorPanel text={state.error} onRetry={reload} /></>;
  const c = state.data;
  const { a, b } = c;
  const na = a.numbers;
  const nb = b.numbers;
  return (
    <>
      {picker}
      {!a.has_report || !b.has_report ? (
        <Empty title={`Round ${!b.has_report ? b.number : a.number} has no analyzed surveys yet`}>
          The comparison fills in as soon as the first surveys of that round are analyzed.
        </Empty>
      ) : (
        <>
          <div className="org-hero">
            <div>
              <p className="eyebrow">Result</p>
              <h3 className="org-hero-title">{c.headline || `Round ${a.number} compared with Round ${b.number}.`}</h3>
            </div>
            <button type="button" className="btn btn-secondary" onClick={() => downloadComparisonPdf({ product, comparison: c })}>
              <Icon name="download" /> Comparison PDF
            </button>
          </div>
          <div className="org-compare-grid">
            <section className="panel org-panel">
              <h3 className="org-panel-title">Key numbers</h3>
              <div className="table-wrap">
                <table className="data-table org-num-table">
                  <thead>
                    <tr><th scope="col" /><th scope="col">Round {a.number}</th><th scope="col">Round {b.number}</th><th scope="col">Change</th></tr>
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
            <section className="panel org-panel">
              <h3 className="org-panel-title">What consumers said</h3>
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
                          <td>{r.name}</td>
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
            <section className="panel org-panel org-span">
              <h3 className="org-panel-title">Recipe used: v{a.recipe_version ?? "—"} → v{b.recipe_version ?? "—"}</h3>
              {c.recipe_changes.length ? (
                <ul className="org-changes">{c.recipe_changes.map((x) => <li key={x}>{x}</li>)}</ul>
              ) : (
                <p className="muted">{c.same_recipe ? "Same recipe in both rounds." : "No recipe details to compare."}</p>
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

function UpdatesPage({ onSeen }) {
  const [state, reload] = useData(() => getUpdates(200), []);
  useEffect(() => {
    markUpdatesSeen().then(onSeen).catch(() => {});
  }, [onSeen]);
  return (
    <section aria-labelledby="org-updates-page">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="org-updates-page">Updates</h1>
          <p className="home-sub">Everything on your products, newest first.</p>
        </div>
      </header>
      {state.loading && !state.data ? (
        <Loading />
      ) : state.error ? (
        <ErrorPanel text={state.error} onRetry={reload} />
      ) : (
        <section className="panel org-panel">
          <UpdatesList items={state.data.updates} showOpen />
        </section>
      )}
    </section>
  );
}

/* ================================================================
   Account
   ================================================================ */

function AccountPage() {
  const [state, reload] = useData(getOrgAccount, []);
  const [form, setForm] = useState({ current: "", next: "", repeat: "" });
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const change = async (e) => {
    e.preventDefault();
    const problem = !form.current
      ? "Enter your current password."
      : passwordProblem(form.next) || (form.next !== form.repeat ? "The two new passwords are different." : "");
    if (problem) {
      setMsg({ tone: "error", text: problem });
      return;
    }
    setBusy(true);
    try {
      await changePassword(API_BASE_URL, form.current, form.next);
      setForm({ current: "", next: "", repeat: "" });
      setMsg({ tone: "ok", text: "Password changed. Every other sign-in of your organization has ended." });
    } catch (err) {
      setMsg({ tone: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  };

  const everywhere = async () => {
    const ok = await confirmDialog({
      title: "Sign out everywhere?",
      message: "Every sign-in of your organization ends, on every computer, including this one.",
      confirmLabel: "Sign out everywhere",
      danger: true,
    });
    if (ok) await signOutEverywhere(API_BASE_URL);
  };

  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel text={state.error} onRetry={reload} />;
  const a = state.data;
  return (
    <section aria-labelledby="org-account-title">
      <button type="button" className="back-link" onClick={() => window.history.back()}><Icon name="back" /> Back</button>
      <header className="home-head">
        <div className="page-heading">
          <h1 id="org-account-title">Account</h1>
        </div>
      </header>
      <div className="org-two is-even">
        <section className="panel org-panel">
          <h2 className="org-panel-title">Organization</h2>
          <dl className="org-dl">
            <dt>Name</dt>
            <dd>{a.name}</dd>
            <dt>Sign-in email</dt>
            <dd>{a.email || a.mobile || "—"}</dd>
            <dt>Products</dt>
            <dd>{a.products}</dd>
            <dt>Last sign-in</dt>
            <dd>{fmtDateTime(a.last_login_at) || "—"}</dd>
            <dt>Your contact</dt>
            <dd>{a.contact}</dd>
          </dl>
          <p className="muted small">To change the name or the sign-in email, contact {a.contact}.</p>
        </section>
        <section className="panel org-panel">
          <form onSubmit={change} noValidate>
            <h2 className="org-panel-title">Password</h2>
            <label className="field">
              <span className="field-label">Current password</span>
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
            {msg && <p className={msg.tone === "error" ? "form-error" : "form-ok"} role={msg.tone === "error" ? "alert" : "status"}>{msg.text}</p>}
            <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>{busy ? "Saving…" : "Change password"}</button>
          </form>
          <hr className="org-divider" />
          <h2 className="org-panel-title">Sign-ins</h2>
          <p className="muted small">Lost a laptop, or someone left the company? End every sign-in at once.</p>
          <button type="button" className="btn btn-secondary btn-sm" onClick={everywhere}>Sign out everywhere</button>
        </section>
      </div>
    </section>
  );
}