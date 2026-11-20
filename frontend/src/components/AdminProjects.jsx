import { useCallback, useEffect, useMemo, useState } from "react";
import { AllocateDialog, PlanCard } from "./ActionPlans";
import KpiIcon from "./KpiIcon";
import { adminProject, adminProjectPlans, adminProjects, orgError } from "../services/orgApi";

/*
  Admin > Projects (backend api/projects.py admin_router).
  Every project the Super Admin created, read-only: the Admin can't edit, start or delete a project,
  nor change its response target or survey plans. Inside a project the Admin allocates the survey
  team of each survey plan (the one place where survey teams are allocated).
  A new project reaches the Admin as a bell notification that opens it here.
*/

const STATUS_TONE = { draft: "is-neutral", ongoing: "is-ok", completed: "is-act" };
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const fmtDate = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";
};

// `key` changes when something else must be loaded (e.g. another project id).
function useLoad(loader, key) {
  const [state, setState] = useState({ loading: true, data: null, error: "" });
  const load = useCallback(() => {
    loader()
      .then((data) => setState({ loading: false, data, error: "" }))
      .catch((e) => setState({ loading: false, data: null, error: orgError(e, "This couldn't be loaded. Try again.") }));
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  return [state, load];
}

export default function AdminProjects({ openId = null, onOpened }) {
  const [selected, setSelected] = useState(openId);
  useEffect(() => {
    if (openId != null) {
      setSelected(openId);
      onOpened?.();
    }
  }, [openId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (selected != null) return <AdminProjectPage projectId={selected} onBack={() => setSelected(null)} />;
  return <AdminProjectList onOpen={setSelected} />;
}

function AdminProjectList({ onOpen }) {
  const [state, reload] = useLoad(() => adminProjects(), "list");
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const all = state.data?.projects || [];
    return needle ? all.filter((p) => [p.project_name, p.project_code, p.category.name, p.product.name, p.version_label]
      .some((x) => (x || "").toLowerCase().includes(needle))) : all;
  }, [state.data, q]);
  if (state.loading) return <section className="panel"><p className="muted">Loading projects…</p></section>;
  if (state.error) {
    return (
      <section className="panel">
        <p className="form-error" role="alert">{state.error}</p>
        <button type="button" className="btn btn-secondary btn-sm" onClick={reload}>Try again</button>
      </section>
    );
  }
  const k = state.data.kpis;
  const cards = [["Total projects", k.total, "all projects"], ["Ongoing", k.ongoing, "survey collecting now"],
    ["Completed", k.completed, "surveys finished"], ["Draft", k.draft, "survey not started yet"]];
  return (
    <section aria-labelledby="admin-projects-title">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="admin-projects-title">Projects</h1>
          <p className="home-sub">The Super Admin's projects. Open one to see its plan and allocate the survey team to each survey plan.</p>
        </div>
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
        {!state.data.projects.length ? (
          <p className="muted">No projects yet. You're notified when the Super Admin creates one.</p>
        ) : (
          <>
            <div className="table-toolbar">
              <input type="search" className="search" placeholder="Search projects, categories, products or recipes" value={q}
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
                    <th scope="col">Response target</th>
                    <th scope="col">Status</th>
                    <th scope="col">Survey</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.id} className="is-clickable" onClick={() => onOpen(p.id)}>
                      <td>
                        <div className="cell-product">
                          <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); onOpen(p.id); }}>{p.project_name}</button>
                          <span>{p.project_code}</span>
                        </div>
                      </td>
                      <td className="col-category">{p.category.name}</td>
                      <td><div className="cell-product"><strong>{p.product.name}</strong><span>{p.product.sku || ""}</span></div></td>
                      <td><span className="pv-chip">{p.version_label}</span></td>
                      <td>{p.total_target ?? <span className="muted">Not set</span>}</td>
                      <td><span className={`status-chip ${STATUS_TONE[p.status]}`}>{p.status_label}</span></td>
                      <td>
                        {p.live_round ? <span className="round-chip is-live">Survey {p.live_round.number} · {p.live_round.done} of {p.live_round.target}</span>
                          : p.rounds ? <span className="muted">{plural(p.rounds, "survey")} done</span> : <span className="muted">Not started</span>}
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

function AdminProjectPage({ projectId, onBack }) {
  const [state, reload] = useLoad(() => Promise.all([adminProject(projectId), adminProjectPlans(projectId)]), projectId);
  const [dialog, setDialog] = useState(null);
  const [notice, setNotice] = useState("");
  if (state.loading) return <section className="panel"><p className="muted">Loading the project…</p></section>;
  if (state.error) {
    return (
      <section className="panel">
        <button type="button" className="back-link" onClick={onBack}>‹ Projects</button>
        <p className="form-error" role="alert">{state.error}</p>
      </section>
    );
  }
  const [project, plan] = state.data;
  const live = plan.stage === "live";
  const unstaffed = plan.plans.filter((p) => !p.members.length).length;
  return (
    <section className="project-page admin-project" aria-labelledby="admin-project-title">
      <button type="button" className="back-link" onClick={onBack}>‹ Projects</button>
      <header className="home-head org-product-head">
        <div className="page-heading">
          <p className="eyebrow">{project.project_code} · {project.category.name} · {project.version_label}</p>
          <h1 id="admin-project-title">
            {project.project_name}
            <span className={`status-chip ${STATUS_TONE[project.status]} project-status`}>{project.status_label}</span>
          </h1>
          <p className="home-sub">Product: {project.product.name}{project.product.sku ? ` · ${project.product.sku}` : ""} · recipe v{project.product_version}</p>
        </div>
        <span className="status-chip is-neutral admin-readonly" title="The Super Admin manages this project">Read only</span>
      </header>
      {notice && <p className="alert alert-ok" role="status">{notice}</p>}
      <section className="panel project-summary">
        {project.description && <p className="project-desc">{project.description}</p>}
        <dl className="ap-facts project-facts">
          <div><dt>Project code</dt><dd>{project.project_code}</dd></div>
          <div><dt>Category</dt><dd>{project.category.name}</dd></div>
          <div><dt>Product</dt><dd>{project.product.name}</dd></div>
          <div><dt>Recipe version</dt><dd>{project.version_label}{project.version?.saved_at ? ` · saved ${fmtDate(project.version.saved_at)}` : ""}</dd></div>
          <div><dt>Response target</dt><dd>{project.total_target ?? "Not set"}{plan.planned ? ` · ${plan.planned} planned` : ""}</dd></div>
        </dl>
      </section>
      <section className="panel" aria-labelledby="admin-plan-title">
        <div className="ap-head">
          <div>
            <h2 id="admin-plan-title" className="report-h">
              Survey plan
              {plan.round ? <span className={`round-chip${live ? " is-live" : ""}`}>Survey {plan.round.number} · {live ? "collecting" : "completed"}</span> : null}
            </h2>
            <p className="muted">
              {plan.plans.length
                ? `${plural(plan.plans.length, "plan")}${live ? ` · ${plan.round.done} of ${plan.round.target} consumers surveyed` : ` · ${plan.target} consumers`}. `
                  + (plan.can_allocate ? (unstaffed ? `${plural(unstaffed, "plan")} still need${unstaffed === 1 ? "s" : ""} a team.` : "Every plan has a team.") : "")
                : "The Super Admin hasn't added survey plans yet."}
            </p>
          </div>
        </div>
        <div className="ap-grid">
          {plan.plans.map((p) => (
            <PlanCard key={p.id} p={p} canEdit={false} canAllocate={plan.can_allocate} onTeam={() => setDialog({ plan: p })} />
          ))}
        </div>
        {!plan.can_allocate && plan.plans.length > 0 && (
          <p className="field-hint">{plan.stage === "ended" ? "This survey has ended: its teams are kept as they ran." : "Teams can't be changed for this project now."}</p>
        )}
      </section>
      {dialog && (
        <AllocateDialog plan={dialog.plan} plans={plan.plans} onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); setNotice(`Team saved for “${dialog.plan.name}”. The survey persons see it once the survey is collecting.`); reload(); }} />
      )}
    </section>
  );
}
