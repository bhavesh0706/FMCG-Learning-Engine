import { useEffect, useMemo, useState } from "react";
import ReportPrompt from "./ReportPrompt";
import { errorText, getReportOptions } from "../services/nextApi";

/* Analytics: Excel / PowerPoint from a prompt across one, several or all products. */
export default function AnalyticsPage() {
  const [opts, setOpts] = useState(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState([]);
  const [q, setQ] = useState("");
  useEffect(() => {
    getReportOptions().then(setOpts).catch((err) => setError(errorText(err, "Products couldn't be loaded.")));
  }, []);
  const groups = useMemo(() => {
    const out = {};
    const needle = q.trim().toLowerCase();
    (opts?.products || []).filter((p) => !needle || `${p.name} ${p.category}`.toLowerCase().includes(needle))
      .forEach((p) => (out[p.category || "Other"] ||= []).push(p));
    return out;
  }, [opts, q]);
  const all = (opts?.products || []).map((p) => p.id);
  const toggle = (id) => setPicked((x) => (x.includes(id) ? x.filter((i) => i !== id) : [...x, id]));
  return (
    <section className="pc" aria-labelledby="analytics-title">
      <div className="home-head">
        <div className="page-heading">
          <h1 id="analytics-title">Analytics</h1>
          <p>PowerPoint reports from a prompt, for one, several or all products. Every number comes from the surveys.</p>
        </div>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="analytics-grid">
        <div className="panel">
          <div className="access-people-head">
            <h2 className="report-h">Products <span className="count-badge">{picked.length}/{all.length}</span></h2>
            <span className="access-people-tools">
              <button type="button" className="link-btn" onClick={() => setPicked(all)}>Select all</button>
              <button type="button" className="link-btn" onClick={() => setPicked([])}>Clear</button>
            </span>
          </div>
          <input className="input search" type="search" placeholder="Search products" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="assign-list">
            {!opts ? <p className="muted">Loading…</p> : Object.entries(groups).map(([cat, items]) => (
              <fieldset key={cat} className="assign-group">
                <legend>{cat}</legend>
                {items.map((p) => (
                  <label key={p.id} className={`assign-item${picked.includes(p.id) ? " is-on" : ""}`}>
                    <input type="checkbox" checked={picked.includes(p.id)} onChange={() => toggle(p.id)} />
                    <span className="assign-item-text"><strong>{p.name}</strong>
                      <span className="muted">{p.rounds.length ? `Rounds ${p.rounds.join(", ")}` : "No rounds yet"}</span></span>
                  </label>
                ))}
              </fieldset>
            ))}
          </div>
          <p className="muted small">Or name the products in the request, e.g. “compare all cereals”.</p>
        </div>
        <div className="panel">
          <h2 className="report-h">PowerPoint</h2>
          <ReportPrompt format="pptx" productIds={picked} autoFocus={false} />
        </div>
      </div>
    </section>
  );
}