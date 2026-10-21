import { useCallback, useEffect, useId, useState } from "react";
import { getAuditLog } from "../services/teamApi";

/*
  Activity log (Team page, admins only): who changed what, and when.
  Backend: GET /admin/audit (app/services/audit.py). The log grows without
  limit, so filtering and paging happen on the server, not in the browser.
*/

const AREAS = [
  ["", "Everything"],
  ["auth.", "Sign-ins and sign-outs"],
  ["user.", "Team changes"],
  ["product.", "Products"],
  ["category.", "Categories"],
  ["question.", "Survey questions"],
  ["recipe.", "Recipes"],
  ["template.", "Question templates"],
  ["survey.", "Surveys"],
  ["recording.", "Recordings"],
  ["report.", "Reports"],
];

const PERIODS = [
  ["7", "Last 7 days"],
  ["30", "Last 30 days"],
  ["90", "Last 90 days"],
  ["", "All time"],
];

const PAGE_SIZES = [20, 50, 100];

// Plain names for the fields shown in "What changed".
const FIELD_LABELS = {
  is_active: "Active",
  can_view_history: "Survey history",
  role: "Role",
  name: "Name",
  age: "Age",
  gender: "Gender",
  ingredients: "Ingredients",
  steps: "Steps",
  batch_size: "Batch size",
  added_products: "Products added",
  removed_products: "Products removed",
  added_people: "People added",
  removed_people: "People removed",
};

function show(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (value === true || value === 1) return "On";
  if (value === false || value === 0) return "Off";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "none";
  return String(value);
}

function changeLines(details) {
  if (!details || typeof details !== "object") return [];
  return Object.entries(details)
    .filter(([, v]) => !(Array.isArray(v) && v.length === 0))
    .map(([key, v]) => {
      const label = FIELD_LABELS[key] || key.replaceAll("_", " ");
      // [old, new] pairs from the server; lists of ids for added/removed.
      if (Array.isArray(v) && v.length === 2 && !key.startsWith("added_") && !key.startsWith("removed_")) {
        const counts = key === "ingredients" || key === "steps";
        return `${label}: ${counts ? v[0] : show(v[0])} → ${counts ? v[1] : show(v[1])}`;
      }
      return `${label}: ${show(v)}`;
    });
}

function when(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function roleLabel(role) {
  if (role === "admin") return "Admin";
  if (role === "executive") return "Survey person";
  return "";
}

export default function ActivityLog() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState(""); // what was actually searched (after typing stops)
  const [area, setArea] = useState("");
  const [days, setDays] = useState("30");
  const [pageSize, setPageSize] = useState(20);
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ loading: true, error: "", total: 0, entries: [] });
  const areaId = useId();
  const periodId = useId();
  const sizeId = useId();

  // Search when typing pauses, not on every key.
  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => setPage(1), [query, area, days, pageSize]);

  const load = useCallback(async () => {
    setData((d) => ({ ...d, loading: true, error: "" }));
    try {
      const res = await getAuditLog({ q: query, action: area, days, limit: pageSize, offset: (page - 1) * pageSize });
      setData({ loading: false, error: "", total: res.total || 0, entries: res.entries || [] });
    } catch (err) {
      setData((d) => ({
        ...d,
        loading: false,
        error: err?.response?.status === 404 ? "Update the backend to see the activity log." : "The activity log couldn't be loaded. Try again.",
      }));
    }
  }, [query, area, days, pageSize, page]);

  useEffect(() => {
    load();
  }, [load]);

  const pages = Math.max(1, Math.ceil(data.total / pageSize));
  const from = data.total ? (page - 1) * pageSize + 1 : 0;
  const to = Math.min(page * pageSize, data.total);

  return (
    <section className="panel home-products activity-log" aria-labelledby="activity-title">
      <div className="activity-head">
        <h2 id="activity-title">Activity log</h2>
        <p className="muted">
          Who changed what, and when: sign-ins, team and catalog changes, recipes, surveys and deletions. Entries can't
          be edited. Customer names and phone numbers are never recorded here.
        </p>
      </div>

      <div className="table-toolbar">
        <input type="search" className="search" placeholder="Search by person or description" value={q}
          onChange={(e) => setQ(e.target.value)} aria-label="Search the activity log" />
        <label className="visually-hidden" htmlFor={areaId}>Area</label>
        <select id={areaId} className="select toolbar-select" value={area} onChange={(e) => setArea(e.target.value)}>
          {AREAS.map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <label className="visually-hidden" htmlFor={periodId}>Period</label>
        <select id={periodId} className="select toolbar-select" value={days} onChange={(e) => setDays(e.target.value)}>
          {PERIODS.map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <button type="button" className="btn btn-secondary" onClick={load} disabled={data.loading}>
          {data.loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {data.error ? (
        <div className="catalog-empty" role="alert">
          <strong>Couldn't load the activity log</strong>
          <p>{data.error}</p>
          <button type="button" className="btn btn-secondary" onClick={load}>Try again</button>
        </div>
      ) : data.loading && !data.entries.length ? (
        <p className="muted table-empty">Loading…</p>
      ) : data.entries.length === 0 ? (
        <p className="muted table-empty">Nothing recorded for these filters.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table activity-table">
            <caption className="visually-hidden">Activity log, newest first</caption>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Who</th>
                <th scope="col">What happened</th>
                <th scope="col" className="col-category">Address</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((e) => {
                const lines = changeLines(e.details);
                const failed = e.action === "auth.login_failed" || e.action === "auth.login_refused";
                return (
                  <tr key={e.id}>
                    <td className="activity-when">{when(e.at)}</td>
                    <td>
                      <div className="cell-product">
                        <strong>{e.user_name || (failed ? "Unknown" : "—")}</strong>
                        <span>{roleLabel(e.user_role)}</span>
                      </div>
                    </td>
                    <td>
                      <div className="activity-what">
                        <span className={failed ? "activity-warn" : ""}>{e.summary || e.action}</span>
                        {lines.length > 0 && (
                          <ul className="activity-changes">
                            {lines.map((line) => (
                              <li key={line}>{line}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </td>
                    <td className="col-category muted">{e.ip || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {data.total > 0 && (
        <nav className="pager" aria-label="Activity log pages">
          <span className="muted">
            Showing {from.toLocaleString()}–{to.toLocaleString()} of {data.total.toLocaleString()}
          </span>
          <div className="pager-controls">
            <div className="pager-size">
              <label htmlFor={sizeId}>Rows per page</label>
              <select id={sizeId} className="select select-sm" value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </div>
            <div className="pager-buttons">
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setPage(page - 1)} disabled={page <= 1}>
                Previous
              </button>
              <span className="pager-page" aria-current="page">Page {page} of {pages}</span>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setPage(page + 1)} disabled={page >= pages}>
                Next
              </button>
            </div>
          </div>
        </nav>
      )}
    </section>
  );
}
