import { useCallback, useEffect, useMemo, useState } from "react";
import Modal from "./Modal";
import Select from "./Select";
import KpiIcon from "./KpiIcon";
import {
  addMasterValue, createMasterTable, deleteMasterTable, listMasterTables, listMasterValues, masterError,
  updateMasterTable, updateMasterValue,
} from "../services/otherMastersApi";

/*
  Admin > Other Master: the master tables behind every list in the app
  (m_othermastername) and their values (m_othermasterdata).
    Active    the table can be used (inactive tables leave every list and take no new values)
    Can Edit  existing values may be changed
    Add Value new values may be added
  The server enforces the same rules (backend api/other_masters.py); here they only shape the screen.
*/

const fmt = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit", year: "numeric" }) : "—";
};

/* One permission: a compact pill with its name and an Open / Closed switch (what it does: on hover). */
function Permission({ checked, onChange, title, text, disabled = false, hint }) {
  return (
    <label className={`om-perm${checked ? " is-on" : ""}${disabled ? " is-disabled" : ""}`} title={hint || text}>
      <strong>{title}</strong>
      <button type="button" role="switch" aria-checked={checked} aria-label={`${title}: ${text}`} disabled={disabled}
        className={`om-switch-track${checked ? " is-on" : ""}`} onClick={() => onChange(!checked)}>
        <span className="om-switch-knob" />
      </button>
    </label>
  );
}

/* Create or edit a master table. */
function TableDialog({ table, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: table?.name || "", description: table?.description || "",
    is_active: table ? table.is_active : true, can_edit: table ? table.can_edit : true, allow_add: table ? table.allow_add : true,
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const sys = Boolean(table?.is_system);
  const save = async () => {
    if (form.name.trim().length < 2) return setError("Enter the table name (at least 2 characters).");
    if (!form.description.trim()) return setError("Enter a description.");
    setBusy(true);
    setError("");
    try {
      const body = { ...form, name: form.name.trim(), description: form.description.trim() };
      onSaved(table ? await updateMasterTable(table.id, body) : await createMasterTable(body));
    } catch (e) {
      setError(masterError(e, "The table couldn't be saved."));
      setBusy(false);
    }
  };
  return (
    <Modal title={table ? `Edit “${table.name}”` : "Create master table"} subtitle={table ? "Change its details and what people may do with it." : "A new list of values people can pick from in the app."}
      onClose={onClose}
      footer={<>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
      </>}>
      <div className="om-dialog">
        <div className="om-context">
          <span className="om-context-chip">{table ? `Table ID ${table.id}` : "Table ID: assigned on save"}</span>
          {table?.code && <span className="om-context-chip is-code">{table.code}</span>}
          {sys && <span className="om-context-chip is-warn">Built in</span>}
        </div>

        <section className="om-section" aria-label="Details">
          <h3 className="om-section-title">Details</h3>
          <label className="field">
            <span className="field-label">Table name <span className="req" aria-hidden="true">*</span></span>
            <input className="input" value={form.name} maxLength={150} autoFocus placeholder="e.g. Packaging type"
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="field">
            <span className="field-label">Description <span className="req" aria-hidden="true">*</span></span>
            <textarea className="input" rows={2} maxLength={2000} value={form.description} placeholder="What this list holds and where it's used"
              onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
        </section>

        <section className="om-section" aria-label="Permissions">
          <h3 className="om-section-title">Permissions</h3>
          <div className="om-perms">
            <Permission title="Active" text="The table can be used across the app." checked={form.is_active} disabled={sys}
              hint={sys ? "Used by the program: always active" : ""} onChange={(v) => setForm({ ...form, is_active: v })} />
            <Permission title="Can Edit" text="Existing values can be changed." checked={form.can_edit}
              onChange={(v) => setForm({ ...form, can_edit: v })} />
            <Permission title="Add Value" text="New values can be added." checked={form.allow_add} disabled={sys}
              hint={sys ? "Used by the program: its values are fixed" : ""} onChange={(v) => setForm({ ...form, allow_add: v })} />
          </div>
          {sys && <p className="field-hint">A system table: the program relies on its codes, so it stays active and takes no new values.</p>}
        </section>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

/* Add a value, or edit one (its code stays: saved data uses it). */
function ValueDialog({ table, value, parentOptions, onClose, onSaved }) {
  const [form, setForm] = useState({
    code: value?.code || "", description: value?.description || "", is_active: value ? value.is_active : true,
    parent_id: value?.parent?.id ? String(value.parent.id) : (parentOptions[0] ? String(parentOptions[0].id) : ""),
    factor: value?.extra?.to_base != null ? String(value.extra.to_base) : "",
  });
  // A value inside a category with a base unit (Measurement unit: kg / L) is a unit of measure:
  // it has a conversion factor, which every recipe calculation uses.
  const parentOpt = parentOptions.find((p) => String(p.id) === form.parent_id);
  const base = value ? value.extra?.base : parentOpt?.extra?.base;
  const factorNum = Number(String(form.factor).replace(/,/g, ""));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!value && !form.code.trim()) return setError("Enter the value code.");
    if (base && !(factorNum > 0)) return setError(`Enter the conversion factor: 1 ${form.code.trim() || "unit"} = how many ${base}?`);
    setBusy(true);
    setError("");
    try {
      onSaved(value
        ? await updateMasterValue(value.id, { description: form.description.trim(), is_active: form.is_active, ...(base ? { factor: factorNum } : {}) })
        : await addMasterValue(table.id, {
          code: form.code.trim(), description: form.description.trim() || null, is_active: form.is_active,
          parent_id: table.parent ? Number(form.parent_id) || null : null,
          ...(base ? { factor: factorNum } : {}),
        }));
    } catch (e) {
      setError(masterError(e, "The value couldn't be saved."));
      setBusy(false);
    }
  };
  return (
    <Modal title={value ? `Edit “${value.code}”` : `Add a value to ${table.name}`} subtitle={value ? `A value of ${table.name}` : "It appears in every list that uses this table."} onClose={onClose}
      footer={<>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
      </>}>
      <div className="om-dialog">
        <div className="om-context">
          <span className="om-context-chip">Table: {table.name}</span>
          <span className="om-context-chip">{value ? `Data ID ${value.id}` : "Data ID: assigned on save"}</span>
        </div>

        <section className="om-section" aria-label="Value">
          <h3 className="om-section-title">Value</h3>
          {table.parent && (
            <label className="field">
              <span className="field-label">{table.parent.name} <span className="req" aria-hidden="true">*</span></span>
              <Select className="input" value={form.parent_id} disabled={Boolean(value)} onChange={(e) => setForm({ ...form, parent_id: e.target.value })}>
                {parentOptions.map((p) => <option key={p.id} value={String(p.id)}>{p.description}</option>)}
              </Select>
            </label>
          )}
          <div className="om-row-2">
            <label className="field">
              <span className="field-label">Value code <span className="req" aria-hidden="true">*</span></span>
              <input className="input" value={form.code} maxLength={80} autoFocus={!value} readOnly={Boolean(value)} disabled={Boolean(value)}
                placeholder="e.g. FIXED" onChange={(e) => setForm({ ...form, code: e.target.value })} />
              <span className="field-hint">{value ? "Kept as it is: saved data uses it." : "Stored by the app; can't change later."}</span>
            </label>
            <label className="field">
              <span className="field-label">Value description</span>
              <input className="input" value={form.description} maxLength={255} autoFocus={Boolean(value)} placeholder="e.g. Fixed"
                onChange={(e) => setForm({ ...form, description: e.target.value })} />
              <span className="field-hint">What people see in lists.</span>
            </label>
          </div>
        </section>

        {base && (
          <section className="om-section" aria-label="Conversion">
            <h3 className="om-section-title">Conversion <span className="req" aria-hidden="true">*</span></h3>
            <div className="om-factor-box">
              <span className="om-factor-unit">1 {form.code.trim() || "unit"}</span>
              <span className="om-factor-eq">=</span>
              <input className="input" inputMode="decimal" value={form.factor} placeholder="e.g. 0.001"
                onChange={(e) => setForm({ ...form, factor: e.target.value.replace(/[^0-9.,eE-]/g, "") })} aria-label={`How many ${base} in one unit`} />
              <span className="om-factor-unit">{base}</span>
            </div>
            <p className="field-hint">Used by the batch weight, costing and recipe standards.{base === "L" ? " 1 L is counted as 1 kg for batch weights." : ""}</p>
          </section>
        )}

        <section className="om-section" aria-label="Status">
          <h3 className="om-section-title">Status</h3>
          <div className="om-perms is-one">
            <Permission title="Active" text="Shown in the app's lists. Inactive values stay on saved data." checked={form.is_active}
              disabled={table.is_system} onChange={(v) => setForm({ ...form, is_active: v })} />
          </div>
        </section>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

/* Deleting a table created here removes its values too: type the name to confirm. */
function DeleteTableDialog({ table, onClose, onDeleted }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ok = typed.trim() === table.name.trim();
  const go = async () => {
    setBusy(true);
    setError("");
    try {
      await deleteMasterTable(table.id);
      onDeleted();
    } catch (e) {
      setError(masterError(e, "The table couldn't be deleted."));
      setBusy(false);
    }
  };
  return (
    <Modal title={`Delete “${table.name}”?`} subtitle="This can't be undone." onClose={onClose}
      footer={<>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-danger" disabled={!ok || busy} onClick={go}>{busy ? "Deleting…" : "Delete table"}</button>
      </>}>
      <p>The table and its {table.values} {table.values === 1 ? "value" : "values"} are deleted permanently.
        Lists that use it lose these choices. To keep them but stop using the table, make it inactive instead.</p>
      <label className="field">
        <span className="field-label">Type <strong>{table.name}</strong> to confirm</span>
        <input className="input" value={typed} autoFocus onChange={(e) => setTyped(e.target.value)} />
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
    </Modal>
  );
}

const PAGE_SIZES = [10, 20, 50, 0]; // 0 = all on one page

/* The same pager as the Home page's tables (App.jsx Pager, styles .pager). */
function Pager({ total, page, pages, size, onSize, onPage }) {
  const from = total ? (page - 1) * (size || total) + 1 : 0;
  const to = size ? Math.min(page * size, total) : total;
  return (
    <nav className="pager" aria-label="Values pages">
      <span className="muted">Showing {from}–{to} of {total}</span>
      <div className="pager-controls">
        <div className="pager-size">
          <label htmlFor="om-page-size">Rows per page</label>
          <Select id="om-page-size" className="select select-sm" value={size} onChange={(e) => onSize(Number(e.target.value))}>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n > 0 ? n : "All"}</option>)}
          </Select>
        </div>
        <div className="pager-buttons">
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => onPage(page - 1)} disabled={page <= 1}>Previous</button>
          <span className="pager-page" aria-current="page">Page {page} of {pages}</span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => onPage(page + 1)} disabled={page >= pages}>Next</button>
        </div>
      </div>
    </nav>
  );
}

export default function OtherMasters() {
  const [tables, setTables] = useState(null);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null); // { table, values, parent_options }
  const [dialog, setDialog] = useState(null);
  const [notice, setNotice] = useState("");
  const [vq, setVq] = useState(""); // search in the selected table's values
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(() => {
    try { const saved = localStorage.getItem("fmcg.om.pageSize"); return saved !== null && PAGE_SIZES.includes(Number(saved)) ? Number(saved) : 10; } catch { return 10; }
  });
  const changeSize = (n) => {
    setSize(n);
    setPage(1);
    try { localStorage.setItem("fmcg.om.pageSize", String(n)); } catch { /* storage blocked */ }
  };
  const openTable = (id) => { setSelectedId(id); setNotice(""); setVq(""); setPage(1); };

  const loadTables = useCallback(async () => {
    try {
      const d = await listMasterTables();
      setTables(d.tables);
      setError("");
      setSelectedId((cur) => cur ?? d.tables[0]?.id ?? null);
    } catch (e) {
      setError(masterError(e, "The master tables couldn't be loaded."));
    }
  }, []);
  const loadDetail = useCallback(async (id) => {
    if (!id) return setDetail(null);
    try {
      setDetail(await listMasterValues(id));
    } catch (e) {
      setError(masterError(e, "The table couldn't be loaded."));
    }
  }, []);
  useEffect(() => { loadTables(); }, [loadTables]);
  useEffect(() => { loadDetail(selectedId); }, [selectedId, loadDetail]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (tables || []).filter((t) => !needle || `${t.name} ${t.code} ${t.description || ""}`.toLowerCase().includes(needle));
  }, [tables, q]);
  const t = detail?.table;
  const allValues = detail?.values || [];
  const needleV = vq.trim().toLowerCase();
  const filtered = needleV
    ? allValues.filter((v) => `${v.code} ${v.description || ""} ${v.parent?.description || ""}`.toLowerCase().includes(needleV))
    : allValues;
  const pages = size ? Math.max(1, Math.ceil(filtered.length / size)) : 1;
  const current = Math.min(page, pages);
  const pageValues = size ? filtered.slice((current - 1) * size, current * size) : filtered;
  const isUnits = Boolean(detail?.parent_options?.some((p) => p.extra?.base)); // a table of units of measure
  const addProblem = !t ? "" : !t.is_active ? "This table is inactive." : t.is_system ? "A system table: its values are fixed."
    : !t.allow_add ? "Add Value is closed for this table." : "";
  const editProblem = !t ? "" : !t.is_active ? "This table is inactive." : !t.can_edit ? "Can Edit is closed for this table." : "";
  const refresh = async (msg) => {
    setNotice(msg || "");
    await Promise.all([loadTables(), loadDetail(selectedId)]);
  };
  const toggleValue = async (v) => {
    try {
      await updateMasterValue(v.id, { is_active: !v.is_active });
      refresh(`“${v.code}” is now ${v.is_active ? "inactive" : "active"}.`);
    } catch (e) {
      setError(masterError(e, "The value couldn't be changed."));
    }
  };

  return (
    <section className="catalog om-page" aria-labelledby="om-title">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="om-title">Master</h1>
          <p className="home-sub">The lookup tables behind every list in the app, and their values.</p>
        </div>
        <div className="catalog-head-actions">
          <button type="button" className="btn btn-primary" onClick={() => setDialog({ type: "table" })}>Create table</button>
        </div>
      </header>

      {notice && (
        <p className="alert alert-ok org-notice" role="status">
          <span>{notice}</span>
          <button type="button" className="link-btn" onClick={() => setNotice("")}>Dismiss</button>
        </p>
      )}
      {error && (
        <div className="catalog-notice is-error" role="alert">
          <span>{error}</span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setError(""); refresh(""); }}>Try again</button>
        </div>
      )}

      <div className="home-stats page-kpis">
        <div className="h-stat kpi-card tone-brand">
          <KpiIcon name="categories" />
          <span className="h-stat-label">Master tables</span>
          <span className="h-stat-value">{tables ? tables.length : "—"}</span>
          <span className="h-stat-note">{tables ? `${tables.filter((x) => x.is_active).length} active` : "loading…"}</span>
        </div>
        <div className="h-stat kpi-card tone-teal">
          <KpiIcon name="questions" />
          <span className="h-stat-label">Values in this table</span>
          <span className="h-stat-value">{t ? t.values : "—"}</span>
          <span className="h-stat-note">{t ? `${t.active_values} active` : "choose a table"}</span>
        </div>
        <div className="h-stat kpi-card tone-act">
          <KpiIcon name="analyzed" />
          <span className="h-stat-label">Created here</span>
          <span className="h-stat-value">{tables ? tables.filter((x) => !x.built_in).length : "—"}</span>
          <span className="h-stat-note">the rest are built into the app</span>
        </div>
      </div>

      <div className="catalog-layout">
        <aside className="panel catalog-sidebar" aria-label="Master tables">
          <p className="sidebar-label">Master tables</p>
          <input type="search" className="search om-side-search" placeholder="Search tables" value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="Search tables" />
          {!tables ? <p className="muted">Loading…</p> : (
            <ul className="sidebar-list">
              {shown.map((x) => (
                <li key={x.id}>
                  <button type="button" className={`${x.id === selectedId ? "is-on" : ""}${x.is_active ? "" : " is-inactive"}`}
                    onClick={() => openTable(x.id)}>
                    <span>{x.name}</span>
                    <span className="sidebar-count">{x.is_active ? x.values : <span className="status-chip is-neutral">Inactive</span>}</span>
                  </button>
                </li>
              ))}
              {!shown.length && <li className="muted small">No tables match “{q}”.</li>}
            </ul>
          )}
          <button type="button" className="sidebar-add" onClick={() => setDialog({ type: "table" })}>+ Create table</button>
        </aside>

        <section className="panel catalog-main" aria-labelledby="om-table-title">
          {!t ? <p className="muted table-empty">Choose a master table.</p> : (
            <>
              <div className="catalog-main-head">
                <div>
                  <h2 className="panel-title" id="om-table-title">
                    {t.name}
                    {t.built_in && <span className="status-chip is-neutral om-chip">Built in</span>}
                    {!t.is_active && <span className="status-chip is-neutral om-chip">Inactive</span>}
                  </h2>
                  <p className="muted">Table ID {t.id} · {t.code}{t.parent ? ` · values belong to ${t.parent.name}` : ""}</p>
                  {t.description && <p className="muted">{t.description}</p>}
                  <p className="om-flags">
                    <span className={`status-chip ${t.is_active ? "is-ok" : "is-neutral"}`}>Active: {t.is_active ? "Yes" : "No"}</span>
                    <span className={`status-chip ${t.can_edit ? "is-ok" : "is-neutral"}`}>Can edit: {t.can_edit ? "Yes" : "No"}</span>
                    <span className={`status-chip ${t.allow_add && !t.is_system ? "is-ok" : "is-neutral"}`}>Add value: {t.allow_add && !t.is_system ? "Yes" : "No"}</span>
                  </p>
                </div>
                <div className="catalog-main-actions">
                  <button type="button" className="btn btn-primary btn-sm" disabled={Boolean(addProblem)} title={addProblem}
                    onClick={() => setDialog({ type: "value" })}>Add value</button>
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => setDialog({ type: "table", table: t })}>Edit table</button>
                  {!t.built_in && (
                    <button type="button" className="btn btn-danger btn-sm" onClick={() => setDialog({ type: "delete" })}>Delete table</button>
                  )}
                </div>
              </div>
              {addProblem && <p className="field-hint om-why">{addProblem} New values can't be added.</p>}

              {allValues.length > 0 && (
                <div className="table-toolbar">
                  <input type="search" className="search" placeholder={`Search ${t.name.toLowerCase()} values`} value={vq}
                    onChange={(e) => { setVq(e.target.value); setPage(1); }} aria-label="Search values" />
                </div>
              )}
              {allValues.length > 0 && filtered.length === 0 ? (
                <p className="muted table-empty">No values match “{vq}”.</p>
              ) : detail.values.length === 0 ? (
                <div className="catalog-empty">
                  <strong>No values yet</strong>
                  <p>{addProblem || "Add the first value of this table."}</p>
                </div>
              ) : (
                <div className="table-wrap">
                  <table className="data-table om-table">
                    <thead>
                      <tr>
                        <th scope="col">ID</th>
                        {t.parent && <th scope="col">{t.parent.name}</th>}
                        <th scope="col">Value code</th>
                        <th scope="col">Description</th>
                        {isUnits && <th scope="col">Conversion</th>}
                        <th scope="col">Status</th>
                        <th scope="col">Last changed</th>
                        <th scope="col"><span className="visually-hidden">Actions</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {pageValues.map((v) => (
                        <tr key={v.id} className={v.is_active ? "" : "is-muted"}>
                          <td className="muted">{v.id}</td>
                          {t.parent && <td>{v.parent?.description || "—"}</td>}
                          <td><strong>{v.code}</strong></td>
                          <td>{v.description && v.description !== v.code ? v.description : <span className="muted">—</span>}</td>
                          {isUnits && <td>{v.extra?.to_base != null ? `1 ${v.code} = ${Number(v.extra.to_base).toLocaleString(undefined, { maximumSignificantDigits: 12 })} ${v.extra.base || ""}` : "—"}</td>}
                          <td><span className={`status-chip ${v.is_active ? "is-ok" : "is-neutral"}`}>{v.is_active ? "Active" : "Inactive"}</span></td>
                          <td className="muted small">{fmt(v.mod_at || v.created_at)}{(v.mod_by || v.created_by) ? ` · ${v.mod_by || v.created_by}` : ""}</td>
                          <td className="om-row-actions">
                            <button type="button" className="link-btn" disabled={Boolean(editProblem)} title={editProblem}
                              onClick={() => setDialog({ type: "value", value: v })}>Edit</button>
                            {!t.is_system && (
                              <button type="button" className="link-btn" disabled={Boolean(editProblem)} title={editProblem}
                                onClick={() => toggleValue(v)}>{v.is_active ? "Deactivate" : "Activate"}</button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <Pager total={filtered.length} page={current} pages={pages} size={size} onSize={changeSize} onPage={setPage} />
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {dialog?.type === "delete" && t && (
        <DeleteTableDialog table={t} onClose={() => setDialog(null)}
          onDeleted={() => { setDialog(null); setSelectedId(null); setDetail(null); setNotice(`“${t.name}” and its values were deleted.`); loadTables(); }} />
      )}
      {dialog?.type === "table" && (
        <TableDialog table={dialog.table} onClose={() => setDialog(null)}
          onSaved={(saved) => {
            setDialog(null);
            setSelectedId(saved.id);
            refresh(dialog.table ? `“${saved.name}” was saved.` : `“${saved.name}” was created (ID ${saved.id}). Add its values below.`);
          }} />
      )}
      {dialog?.type === "value" && t && (
        <ValueDialog table={t} value={dialog.value} parentOptions={detail.parent_options || []} onClose={() => setDialog(null)}
          onSaved={(v) => { setDialog(null); refresh(dialog.value ? `“${v.code}” was saved.` : `“${v.code}” was added to ${t.name}.`); }} />
      )}
    </section>
  );
}
