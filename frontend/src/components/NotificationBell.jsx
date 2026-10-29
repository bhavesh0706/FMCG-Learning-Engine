import { useCallback, useEffect, useRef, useState } from "react";
import {
  getNotifications,
  getUnreadCount,
  markAllNotificationsRead,
  markNotificationRead,
} from "../services/notificationsApi";

// Styles: "Notification bell" section at the end of App.css.

/*
  Bell in the top bar, for every signed-in person.
    Admins:          "Survey submitted without a video" (survey person, product, times)
                     "Recipe updated by <organization>", "<organization> started / ended Round N"
    Survey persons:  "New product: <name>" when an admin assigns them a product,
                     "New round: <product>" when the organization starts a round

  Stays current without a page reload: the unread count is checked every 30 s,
  when the browser tab comes back into view, and right after the panel opens.

  Props:
    onOpenHistory()  optional, admins: open Survey history from a missing-video notice
    onOpenSurvey()   optional, survey persons: start a survey from a new-product notice
    onOpenRecipe(id) optional, admins: open a product's recipe (and its version history)
                     from a "Recipe updated by the organization" notice
    onOpenRounds(id) optional, admins: open a product's survey rounds from a round notice
    onOpenProduct(id) optional, admins: open a product the organization added or changed
*/

const POLL_MS = 30000;

const MISSING_VIDEO = "survey_missing_video";
const PRODUCT_ASSIGNED = "product_assigned";
const CLIENT_RECIPE = "client_recipe_updated";
const CLIENT_CATALOG = "client_catalog_changed";
const ROUND_STARTED_BY_ORG = "client_round_started";
const ROUND_ENDED_BY_ORG = "client_round_ended";
const ROUND_STARTED = "round_started";
const ROUND_KINDS = [ROUND_STARTED_BY_ORG, ROUND_ENDED_BY_ORG, ROUND_STARTED];

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : dateTime.format(d);
}

function timeAgo(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const minutes = Math.round((Date.now() - d.getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days < 7 ? `${days} d ago` : formatDate(iso);
}

function BellIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </svg>
  );
}

function KindIcon({ kind }) {
  if (ROUND_KINDS.includes(kind)) {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M21 12a9 9 0 1 1-3-6.7" />
        <path d="M21 4v5h-5" />
      </svg>
    );
  }
  if (kind === CLIENT_CATALOG) {
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M4 8 12 4l8 4v8l-8 4-8-4z" />
        <path d="m4 8 8 4 8-4M12 12v8" />
      </svg>
    );
  }
  if (kind === CLIENT_RECIPE) {
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M14 3v4a1 1 0 0 0 1 1h4" />
        <path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2Z" />
        <path d="M9 13h6M9 17h4" />
      </svg>
    );
  }
  if (kind === MISSING_VIDEO) {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M10.66 6H14a2 2 0 0 1 2 2v2.34l1 1L22 8v8" />
        <path d="M16 16a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h2" />
        <path d="m2 2 20 20" />
      </svg>
    );
  }
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
      <path d="m3.3 7 8.7 5 8.7-5" />
      <path d="M12 22V12" />
    </svg>
  );
}

const dayOnly = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";
};

function Details({ item, onOpenHistory, onOpenSurvey, onOpenRecipe, onOpenRounds, onOpenProduct, onClose }) {
  const d = item.data || {};
  if (ROUND_KINDS.includes(item.kind)) {
    const ended = item.kind === ROUND_ENDED_BY_ORG;
    return (
      <div className="notif-details">
        <dl>
          <dt>Product</dt>
          <dd>
            {d.product_name || "—"}
            {d.category_name && <span className="notif-sub">{d.category_name}</span>}
          </dd>
          <dt>Round</dt>
          <dd>Round {d.round}</dd>
          {ended ? (
            <>
              <dt>Surveyed</dt>
              <dd>{d.done} of {d.target} consumers</dd>
            </>
          ) : (
            <>
              <dt>Recipe</dt>
              <dd>v{d.recipe_version ?? "—"}</dd>
              <dt>Consumers to survey</dt>
              <dd>{d.target}</dd>
              {d.due_date && (
                <>
                  <dt>Wanted by</dt>
                  <dd>{dayOnly(d.due_date)}</dd>
                </>
              )}
              <dt>Started</dt>
              <dd>{formatDate(d.started_at || item.created_at)}</dd>
            </>
          )}
        </dl>
        {d.note && <p className="notif-note">“{d.note}”</p>}
        {onOpenRounds && d.product_id && item.kind !== ROUND_STARTED && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => { onClose(); onOpenRounds(d.product_id); }}>
            Open rounds
          </button>
        )}
        {onOpenSurvey && item.kind === ROUND_STARTED && (
          <button type="button" className="btn btn-primary btn-sm" onClick={() => { onClose(); onOpenSurvey(); }}>
            Take a survey
          </button>
        )}
      </div>
    );
  }
  if (item.kind === CLIENT_CATALOG) {
    const isProduct = d.entity === "product";
    return (
      <div className="notif-details">
        <dl>
          <dt>{isProduct ? "Product" : "Category"}</dt>
          <dd>{d.name || "—"}</dd>
          <dt>Change</dt>
          <dd>{d.action === "deleted" ? "Permanently deleted" : (d.action || "updated").replace(/^./, (c) => c.toUpperCase())}</dd>
          <dt>Changed by</dt>
          <dd>{d.client_name || "—"} <span className="notif-sub">Organization</span></dd>
          <dt>Changed</dt>
          <dd>{formatDate(d.changed_at || item.created_at)}</dd>
        </dl>
        {(d.changes || []).length > 0 && (
          <ul className="notif-changes">
            {d.changes.map((c) => <li key={c}>{c}</li>)}
          </ul>
        )}
        {onOpenProduct && isProduct && d.product_id && d.action !== "deleted" && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => { onClose(); onOpenProduct(d.product_id); }}>
            Open product
          </button>
        )}
      </div>
    );
  }
  if (item.kind === CLIENT_RECIPE) {
    return (
      <div className="notif-details">
        <dl>
          <dt>Product</dt>
          <dd>
            {d.product_name || "—"}
            {d.category_name && <span className="notif-sub">{d.category_name}</span>}
          </dd>
          <dt>Changed by</dt>
          <dd>{d.client_name || "—"} <span className="notif-sub">Organization</span></dd>
          <dt>Recipe version</dt>
          <dd>v{d.version}</dd>
          <dt>Changed</dt>
          <dd>{formatDate(d.changed_at || item.created_at)}</dd>
        </dl>
        {(d.changes || []).length > 0 && (
          <ul className="notif-changes">
            {d.changes.map((c) => <li key={c}>{c}</li>)}
          </ul>
        )}
        {onOpenRecipe && d.product_id && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => { onClose(); onOpenRecipe(d.product_id); }}>
            Open recipe and history
          </button>
        )}
      </div>
    );
  }
  if (item.kind === MISSING_VIDEO) {
    const person = d.survey_person || {};
    const received = d.resolution === "video_received";
    const deleted = d.resolution === "survey_deleted";
    return (
      <div className="notif-details">
        <span className={`status-chip ${received ? "is-ok" : deleted ? "" : "is-warn"}`}>
          {received ? "Video received" : deleted ? "Survey deleted" : "Video still missing"}
        </span>
        <dl>
          <dt>Survey person</dt>
          <dd>
            {person.name || "—"}
            {(person.mobile || person.email) && <span className="notif-sub">{person.mobile || person.email}</span>}
          </dd>
          <dt>Product</dt>
          <dd>
            {d.product_name || "—"}
            {d.category_name && <span className="notif-sub">{d.category_name}</span>}
          </dd>
          <dt>Survey</dt>
          <dd>#{d.survey_id}</dd>
          <dt>Survey location</dt>
          <dd>{d.survey_location || "—"}</dd>
          <dt>Customer's city</dt>
          <dd>{d.respondent_location || "—"}</dd>
          <dt>Submitted</dt>
          <dd>{formatDate(d.submitted_at)}</dd>
          {received && (
            <>
              <dt>Video received</dt>
              <dd>{formatDate(d.resolved_at)}</dd>
            </>
          )}
        </dl>
        {onOpenHistory && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => { onClose(); onOpenHistory(); }}>
            Open survey history
          </button>
        )}
      </div>
    );
  }
  if (item.kind === PRODUCT_ASSIGNED) {
    return (
      <div className="notif-details">
        <dl>
          <dt>Product</dt>
          <dd>{d.product_name || "—"}</dd>
          <dt>Category</dt>
          <dd>{d.category_name || "—"}</dd>
          <dt>Assigned by</dt>
          <dd>{d.assigned_by || "—"}</dd>
          <dt>Assigned on</dt>
          <dd>{formatDate(d.assigned_at || item.created_at)}</dd>
        </dl>
        {onOpenSurvey && (
          <button type="button" className="btn btn-primary btn-sm" onClick={() => { onClose(); onOpenSurvey(); }}>
            Take a survey
          </button>
        )}
      </div>
    );
  }
  return null;
}

export default function NotificationBell({ onOpenHistory, onOpenSurvey, onOpenRecipe, onOpenRounds, onOpenProduct }) {
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(null);
  const [panelTop, setPanelTop] = useState(64);
  const latestSeen = useRef(0);
  const ref = useRef(null);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getNotifications({ limit: 30 });
      setItems(data.notifications || []);
      setUnread(data.unread_count || 0);
      latestSeen.current = data.latest_id || 0;
      setError("");
    } catch {
      setError("Notifications couldn't be loaded. Try again in a moment.");
    } finally {
      setLoading(false);
    }
  }, []);

  // The unread count, and the list too while the panel is open and something new arrived.
  const refresh = useCallback(async () => {
    if (document.visibilityState !== "visible") return;
    try {
      const { unread_count: count = 0, latest_id: latest = 0 } = await getUnreadCount();
      setUnread(count);
      if (open && latest !== latestSeen.current) loadList();
    } catch {
      /* offline or signed out: keep what is shown; the next check tries again */
    }
  }, [open, loadList]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [refresh]);

  useEffect(() => {
    if (open) loadList();
  }, [open, loadList]);

  function togglePanel() {
    // Phones: the panel is fixed to the screen, so place it just below the bell.
    const bottom = ref.current?.getBoundingClientRect().bottom;
    if (bottom) setPanelTop(Math.round(bottom + 8));
    setOpen((o) => !o);
  }

  // Close on outside click or Escape.
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

  async function toggleItem(item) {
    setExpanded((cur) => (cur === item.id ? null : item.id));
    if (item.is_read) return;
    // Show it as read at once; the server confirms the new count.
    setItems((list) => list.map((n) => (n.id === item.id ? { ...n, is_read: true } : n)));
    setUnread((c) => Math.max(0, c - 1));
    try {
      const res = await markNotificationRead(item.id);
      setUnread(res.unread_count ?? 0);
    } catch {
      /* the next refresh corrects the count */
    }
  }

  async function readAll() {
    setItems((list) => list.map((n) => ({ ...n, is_read: true })));
    setUnread(0);
    try {
      await markAllNotificationsRead();
    } catch {
      refresh();
    }
  }

  const badge = unread > 99 ? "99+" : String(unread);
  const label = unread ? `Notifications, ${unread} unread` : "Notifications";

  return (
    <div className="notif" ref={ref}>
      <button type="button" className="notif-bell" aria-label={label} aria-haspopup="dialog" aria-expanded={open}
        onClick={togglePanel}>
        <BellIcon />
        {unread > 0 && <span className="notif-badge" aria-hidden="true">{badge}</span>}
      </button>
      <span className="visually-hidden" aria-live="polite">{unread ? `${unread} unread notifications` : ""}</span>

      {open && (
        <div className="notif-panel" role="dialog" aria-label="Notifications" style={{ "--notif-top": `${panelTop}px` }}>
          <div className="notif-head">
            <strong>Notifications</strong>
            {unread > 0 && (
              <button type="button" className="link-btn" onClick={readAll}>Mark all as read</button>
            )}
          </div>

          {error && <p className="notif-error" role="alert">{error}</p>}

          {!error && loading && items.length === 0 && <p className="notif-empty">Loading…</p>}

          {!error && !loading && items.length === 0 && (
            <p className="notif-empty">You're all caught up. New notifications appear here.</p>
          )}

          {items.length > 0 && (
            <ul className="notif-list">
              {items.map((item) => {
                const isOpen = expanded === item.id;
                return (
                  <li key={item.id} className={`notif-item${item.is_read ? "" : " is-unread"}${item.resolved ? " is-resolved" : ""}`}>
                    <button type="button" className="notif-row" aria-expanded={isOpen} onClick={() => toggleItem(item)}>
                      <span className={`notif-icon kind-${item.kind}`}><KindIcon kind={item.kind} /></span>
                      <span className="notif-text">
                        <span className="notif-title">
                          {item.title}
                          {!item.is_read && <span className="notif-dot" aria-label="unread" />}
                        </span>
                        <span className="notif-message">{item.message}</span>
                        <span className="notif-time">
                          {timeAgo(item.created_at)}
                          {item.data?.resolution === "video_received" && (
                            <span className="notif-resolved-note"> · Video received</span>
                          )}
                        </span>
                      </span>
                    </button>
                    {isOpen && (
                      <Details item={item} onOpenHistory={onOpenHistory} onOpenSurvey={onOpenSurvey} onOpenRecipe={onOpenRecipe} onOpenRounds={onOpenRounds} onOpenProduct={onOpenProduct} onClose={() => setOpen(false)} />
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}