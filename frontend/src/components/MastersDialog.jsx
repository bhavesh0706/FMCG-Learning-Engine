import { useCallback, useEffect, useState } from "react";
import Modal from "./Modal";
import { addMaster, changeMaster, errorText, getMasters } from "../services/nextApi";

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
    </Modal>
  );
}
