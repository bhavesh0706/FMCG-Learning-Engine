import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

/*
  ComboInput: a dropdown that looks and works like <Select> (the field, then a list with a
  search box on top) but also accepts a value that isn't in the list: the search text is
  offered as “Use …” (or “Add …” with multi).

    <ComboInput className="input" value={v} onChange={setV} options={["Salt", "Sugar"]} aria-label="…" />
    <ComboInput multi values={cities} onChange={setCities} options={allCities} placeholder="Any city" />

  Single: onChange(text) when a value is chosen (onPick(text) too).
  multi: values is an array; choosing toggles a value (the list stays open); onChange(array).
*/

const norm = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").trim();
const clean = (s) => String(s || "").replace(/,/g, " ").trim().replace(/\s+/g, " ");

export default function ComboInput({
  value = "", values = [], multi = false, onChange, onPick, options = [], allowCustom = true,
  className = "input", placeholder = "Choose", id, disabled, "aria-label": ariaLabel,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [pos, setPos] = useState(null);
  const button = useRef(null);
  const layer = useRef(null);
  const search = useRef(null);
  const listId = useId();

  const chosen = useMemo(() => new Set((multi ? values : [value]).map(norm).filter(Boolean)), [multi, values, value]);

  const items = useMemo(() => {
    const q = norm(query);
    const seen = new Set();
    const list = [];
    // Chosen values first in multi (so they can be unticked), then the options.
    for (const o of [...(multi ? values : []), ...options]) {
      const k = norm(o);
      if (!k || seen.has(k)) continue;
      seen.add(k);
      if (!q || k.includes(q)) list.push({ label: o, value: o });
    }
    const typed = clean(query);
    if (allowCustom && typed && !seen.has(norm(typed))) list.push({ label: `${multi ? "Add" : "Use"} “${typed}”`, value: typed, custom: true });
    return list;
  }, [query, options, values, multi, allowCustom]);

  const place = useCallback(() => {
    const r = button.current?.getBoundingClientRect();
    if (!r) return;
    const height = Math.min(340, 56 + (options.length + 1) * 38);
    const below = window.innerHeight - r.bottom;
    const up = below < height + 12 && r.top > below;
    setPos({
      left: Math.max(8, Math.min(r.left, window.innerWidth - Math.max(r.width, 240) - 8)),
      width: Math.max(r.width, 240),
      ...(up ? { bottom: window.innerHeight - r.top + 4 } : { top: r.bottom + 4 }),
      maxHeight: Math.max(160, Math.min(height, (up ? r.top : below) - 16)),
    });
  }, [options.length]);

  const openList = (initial = "") => {
    if (disabled) return;
    setQuery(initial);
    setActive(0);
    place();
    setOpen(true);
  };
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };
  const choose = (item) => {
    if (!item) return;
    if (multi) {
      const k = norm(item.value);
      const next = chosen.has(k) ? values.filter((v) => norm(v) !== k) : [...values, item.value];
      onChange?.(next);
      setQuery("");
      search.current?.focus();
      return;
    }
    close();
    onChange?.(item.value);
    onPick?.(item.value);
  };

  useLayoutEffect(() => {
    if (!open) return undefined;
    search.current?.focus();
    const onMove = () => place();
    const onDown = (e) => {
      if (!layer.current?.contains(e.target) && !button.current?.contains(e.target)) close(false);
    };
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    return () => {
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
    };
  }, [open, place]);

  useEffect(() => {
    if (open) layer.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const onListKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); choose(items[active]); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "Tab") close(false);
  };
  const onButtonKey = (e) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); openList(); }
    // Typing on the closed field starts the search with that letter.
    else if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); openList(e.key); }
  };

  const shown = multi ? values.join(", ") : value;

  return (
    <>
      <button
        ref={button}
        type="button"
        id={id}
        className={`${className} ss-trigger`.trim()}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        title={shown || undefined}
        onClick={() => (open ? close() : openList())}
        onKeyDown={onButtonKey}
      >
        <span className={`ss-value${shown ? "" : " is-placeholder"}`}>{shown || placeholder}</span>
      </button>
      {open && pos && createPortal(
        <div ref={layer} className="ss-layer" style={{ left: pos.left, width: pos.width, top: pos.top, bottom: pos.bottom, maxHeight: pos.maxHeight }}
          onKeyDown={onListKey}>
          <div className="ss-search">
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
              <path d="M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm9 3-4.3-4.3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            <input ref={search} type="search" value={query} placeholder={allowCustom ? "Search or type…" : "Search…"}
              aria-label={`Search ${ariaLabel || "options"}`} aria-controls={listId}
              aria-activedescendant={items[active] ? `${listId}-${active}` : undefined}
              onChange={(e) => { setQuery(e.target.value); setActive(0); }} />
          </div>
          <ul id={listId} role="listbox" aria-multiselectable={multi || undefined} className="ss-list" aria-label={ariaLabel}>
            {items.map((o, i) => {
              const on = !o.custom && chosen.has(norm(o.value));
              return (
                <li key={`${o.custom ? "+" : ""}${o.value}`} id={`${listId}-${i}`} data-index={i} role="option" aria-selected={on}
                  className={`ss-option${i === active ? " is-active" : ""}${on ? " is-selected" : ""}${o.custom ? " is-custom" : ""}`}
                  onMouseEnter={() => setActive(i)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => choose(o)}>
                  {multi && !o.custom && <span className="ss-check" aria-hidden="true">{on ? "✓" : ""}</span>}
                  {o.label}
                </li>
              );
            })}
            {!items.length && <li className="ss-empty">{query ? `No match for “${query}”` : "Nothing to choose yet: type a name"}</li>}
          </ul>
        </div>,
        document.body,
      )}
    </>
  );
}
