import { useEffect, useMemo, useState } from "react";
import ReportPrompt from "./ReportPrompt";
import { errorText, getReportOptions } from "../services/nextApi";

/* Analytics: a PowerPoint from a prompt across one, several or all products.
   Laid out as three steps: choose products, describe the deck, download it. */
const STEPS = [
  ["Choose products", "One, several or all of them"],
  ["Describe the deck", "Or pick a ready-made request"],
  ["Download", "A PowerPoint built from the surveys"],
];

export default function AnalyticsPage() {
  const [opts, setOpts] = useState(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState([]);
  const [q, setQ] = useState("");
  useEffect(() => {
    getReportOptions().then(setOpts).catch((err) => setError(errorText(err, "Products couldn't be loaded.")));
  }, []);
  const products = useMemo(() => opts?.products || [], [opts]);
  const groups = useMemo(() => {
    const out = {};
    const needle = q.trim().toLowerCase();
    products.filter((p) => !needle || `${p.name} ${p.category}`.toLowerCase().includes(needle))
      .forEach((p) => (out[p.category || "Other"] ||= []).push(p));
    return out;
  }, [products, q]);
  const all = products.map((p) => p.id);
  const surveys = products.reduce((n, p) => n + (p.rounds?.length || 0), 0);
  const toggle = (id) => setPicked((x) => (x.includes(id) ? x.filter((i) => i !== id) : [...x, id]));
  const toggleGroup = (ids) => setPicked((x) => (ids.every((id) => x.includes(id)) ? x.filter((i) => !ids.includes(i)) : [...new Set([...x, ...ids])]));
  const chosen = products.filter((p) => picked.includes(p.id));
  const step = picked.length ? 2 : 1;

  return (
    <section className="pc an-page" aria-labelledby="analytics-title">
      <div className="home-head">
        <div className="page-heading">
          <h1 id="analytics-title">Analytics</h1>
          <p className="home-sub">PowerPoint reports from a prompt, for one, several or all products. Every number comes from the surveys.</p>
        </div>
      </div>

      <div className="an-hero">
        <div className="an-hero-text">
          <span className="an-hero-eyebrow">Report builder</span>
          <strong>Turn your consumer surveys into a ready-to-present deck</strong>
          <span>{products.length} products · {surveys} {surveys === 1 ? "survey" : "surveys"} with reports available</span>
        </div>
        <ol className="an-steps" aria-label="How it works">
          {STEPS.map(([title, note], i) => (
            <li key={title} className={i + 1 < step ? "is-done" : i + 1 === step ? "is-current" : ""}>
              <span className="an-step-num" aria-hidden="true">{i + 1 < step ? "✓" : i + 1}</span>
              <span><strong>{title}</strong><span>{note}</span></span>
            </li>
          ))}
        </ol>
      </div>

      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="analytics-grid an-grid">
        <section className="panel an-products" aria-labelledby="an-products-title">
          <header className="an-panel-head">
            <span className="an-step-badge">1</span>
            <div>
              <h2 id="an-products-title">Choose products</h2>
              <span className="muted small">{picked.length ? `${picked.length} of ${all.length} selected` : "None selected: the request can name them instead"}</span>
            </div>
            <span className="an-tools">
              <button type="button" className="link-btn" onClick={() => setPicked(all)}>Select all</button>
              {picked.length > 0 && <button type="button" className="link-btn" onClick={() => setPicked([])}>Clear</button>}
            </span>
          </header>
          <input className="input search" type="search" placeholder="Search products" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search products" />
          <div className="an-list">
            {!opts ? <p className="muted">Loading…</p> : Object.entries(groups).map(([cat, items]) => {
              const ids = items.map((p) => p.id);
              const allOn = ids.every((id) => picked.includes(id));
              return (
                <fieldset key={cat} className="an-group">
                  <legend>
                    <span>{cat}</span>
                    <button type="button" className="link-btn" onClick={() => toggleGroup(ids)}>{allOn ? "Unselect" : "Select"} {items.length > 1 ? "all" : ""}</button>
                  </legend>
                  {items.map((p) => {
                    const on = picked.includes(p.id);
                    return (
                      <label key={p.id} className={`an-item${on ? " is-on" : ""}`}>
                        <input type="checkbox" checked={on} onChange={() => toggle(p.id)} />
                        <span className="an-avatar" aria-hidden="true">{on ? "✓" : (p.name || "?")[0]}</span>
                        <span className="an-item-text">
                          <strong>{p.name}</strong>
                          <span className="muted">{p.rounds.length ? `Survey ${p.rounds.join(", ")}` : "No surveys yet"}</span>
                        </span>
                        <span className={`an-pill${p.rounds.length ? "" : " is-empty"}`}>
                          {p.rounds.length} {p.rounds.length === 1 ? "survey" : "surveys"}
                        </span>
                      </label>
                    );
                  })}
                </fieldset>
              );
            })}
            {opts && !Object.keys(groups).length && <p className="muted">No products match “{q}”.</p>}
          </div>
        </section>

        <section className="panel an-request" aria-labelledby="an-request-title">
          <header className="an-panel-head">
            <span className="an-step-badge">2</span>
            <div>
              <h2 id="an-request-title">Describe the deck</h2>
              <span className="muted small">Write what the PowerPoint should show, or start from an example below.</span>
            </div>
            <span className="an-file" aria-hidden="true">PPTX</span>
          </header>
          <div className={`an-scope${chosen.length ? "" : " is-empty"}`}>
            <span className="an-scope-label">Included</span>
            {chosen.length ? chosen.map((p) => (
              <button key={p.id} type="button" className="an-scope-chip" onClick={() => toggle(p.id)} aria-label={`Remove ${p.name}`}>
                {p.name} <span aria-hidden="true">×</span>
              </button>
            )) : <span className="muted small">No products chosen yet: pick them on the left, or name them in your request.</span>}
          </div>
          <ReportPrompt format="pptx" productIds={picked} autoFocus={false} />
        </section>
      </div>
    </section>
  );
}
