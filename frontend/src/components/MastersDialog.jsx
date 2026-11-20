import { useCallback, useEffect, useState } from "react";
import Modal from "./Modal";
import { addMaster, changeMaster, errorText, getMasters } from "../services/nextApi";
import { addLovValue, changeLovValue, getLov, loadLov } from "../services/lov";
import Select from "./Select";

/* Admin: master lists for the respondent form (requirements points 4 and 5). */
const LISTS = [["respondent_type", "Respondent type"], ["occupation", "Occupation"], ["sec_class", "Class (SEC)"]];

export default function MastersDialog({ onClose }) {
  const [lists, setLists] = useState(null);
  const [names, setNames] = useState({});
  const [error, setError] = useState("");
  const load = useCallback(() => getMasters(true).then(setLists).catch((err) => setError(errorText(err, "Lists couldn't be loaded."))), []);
  useEffect(() => { load(); }, [load]);
  const add = async (kind) => {
    const name = (names[kind] || "").trim();
    if (!name) return;
    setError("");
    try {
      await addMaster(kind, name);
      setNames({ ...names, [kind]: "" });
      load();
    } catch (err) {
      setError(errorText(err, "The value couldn't be added."));
    }
  };
  const toggle = async (kind, v) => {
    await changeMaster(kind, v.id, { is_active: !v.is_active }).catch((err) => setError(errorText(err, "Couldn't change it.")));
    load();
  };
  return (
    <Modal title="Master lists" subtitle="The choices survey persons pick from in the respondent form." onClose={onClose}
      footer={<button type="button" className="btn btn-primary" onClick={onClose}>Done</button>}>
      {error && <p className="form-error" role="alert">{error}</p>}
      {!lists ? <p className="muted">Loading…</p> : LISTS.map(([kind, label]) => (
        <fieldset key={kind} className="assign-group">
          <legend>{label}</legend>
          <div className="master-chips">
            {(lists[kind] || []).map((v) => (
              <button key={v.id} type="button" className={`master-chip${v.is_active ? "" : " is-off"}`} title={v.is_active ? "Click to hide" : "Click to show again"}
                onClick={() => toggle(kind, v)}>{v.name}{v.is_active ? "" : " (hidden)"}</button>
            ))}
          </div>
          <div className="pc-actions">
            <input className="input" placeholder={`Add to ${label}`} value={names[kind] || ""} onChange={(e) => setNames({ ...names, [kind]: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && add(kind)} />
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => add(kind)}>Add</button>
          </div>
        </fieldset>
      ))}
      <LovLists onError={setError} />
    </Modal>
  );
}

/* Other lists (LOV): m_othermastername / m_othermasterdata.
   Every list: rename values. Open lists (not used by the program's logic): also add and hide. */
function LovLists({ onError }) {
  const [lov, setLov] = useState(null);
  const [code, setCode] = useState("");
  const [draft, setDraft] = useState({});
  const [newLabel, setNewLabel] = useState("");
  const [parentId, setParentId] = useState("");
  const load = useCallback(() => getLov(true).then((d) => {
    setLov(d);
    setCode((c) => c || Object.keys(d)[0] || "");
  }).catch((err) => onError(errorText(err, "Lists couldn't be loaded."))), [onError]);
  useEffect(() => { load(); }, [load]);

  const done = () => { load(); loadLov(true); }; // refresh this dialog and every screen
  const run = async (promise, fallback) => {
    onError("");
    try {
      await promise;
      done();
      return true;
    } catch (err) {
      onError(errorText(err, fallback));
      return false;
    }
  };
  const rename = (v) => {
    const label = (draft[v.id] ?? v.label).trim();
    if (!label || label === v.label) return;
    run(changeLovValue(v.id, { label }), "Couldn't rename it.");
  };
  const add = async () => {
    const label = newLabel.trim();
    if (label && await run(addLovValue(code, label, undefined, parentId ? Number(parentId) : undefined), "The value couldn't be added.")) setNewLabel("");
  };

  if (!lov) return null;
  const list = lov[code];
  // A list inside another one (STATE in COUNTRY, CITY in STATE): show one parent's values at a time.
  const parentList = list?.parent ? lov[list.parent] : null;
  const shown = !list ? [] : parentList ? list.values.filter((v) => String(v.parent_id) === parentId) : list.values;
  return (
    <fieldset className="assign-group">
      <legend>Other lists</legend>
      <Select className="input" value={code} onChange={(e) => { setCode(e.target.value); setDraft({}); setParentId(""); }} aria-label="List">
        {Object.entries(lov).map(([c, l]) => <option key={c} value={c}>{l.name}</option>)}
      </Select>
      {list && (
        <>
          <p className="muted">{list.is_system
            ? "The app relies on these values: you can rename them, not add or hide them."
            : "Rename, hide (click the chip) or add values."}</p>
          {parentList && (
            <Select className="input" value={parentId} onChange={(e) => { setParentId(e.target.value); setDraft({}); }}
              aria-label={parentList.name}>
              <option value="">{`Choose the ${parentList.name.toLowerCase()}`}</option>
              {parentList.values.map((p) => <option key={p.id} value={String(p.id)}>{p.label}</option>)}
            </Select>
          )}
          {shown.map((v) => (
            <div key={v.id} className="pc-actions">
              <input className="input" value={draft[v.id] ?? v.label} aria-label={`Label of ${v.code}`}
                onChange={(e) => setDraft({ ...draft, [v.id]: e.target.value })}
                onBlur={() => rename(v)} onKeyDown={(e) => e.key === "Enter" && rename(v)} />
              {!list.is_system && (
                <button type="button" className={`master-chip${v.is_active ? "" : " is-off"}`}
                  onClick={() => run(changeLovValue(v.id, { is_active: !v.is_active }), "Couldn't change it.")}>
                  {v.is_active ? "Shown" : "Hidden"}
                </button>
              )}
            </div>
          ))}
          {!list.is_system && (!parentList || parentId) && (
            <div className="pc-actions">
              <input className="input" placeholder={`Add to ${list.name}`} value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
              <button type="button" className="btn btn-secondary btn-sm" onClick={add}>Add</button>
            </div>
          )}
        </>
      )}
    </fieldset>
  );
}
