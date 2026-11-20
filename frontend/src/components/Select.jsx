import { Children, Fragment, isValidElement, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

/*
  Select: a drop-in for <select> with a search box, used for every dropdown in the app.

    <Select className="input" value={v} onChange={(e) => set(e.target.value)} aria-label="…">
      <option value="">Choose…</option>
      {items.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
    </Select>

  Same props as a native select (value, onChange(e) with e.target.value as a string, id,
  name, disabled, className, title, aria-*), same <option> children (also from map() and
  fragments, with disabled options). The list opens in a layer above the page (never cut
  off by a dialog), with a search box, arrow keys, Enter, Escape and Home/End.
*/

const text = (node) => {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement(node)) return text(node.props.children);
  return "";
};

function optionsOf(children) {
  const out = [];
  const walk = (nodes) => Children.forEach(nodes, (child) => {
    if (!isValidElement(child)) return;
    if (child.type === Fragment) return walk(child.props.children);
    if (child.type === "optgroup") return walk(child.props.children);
    if (child.type === "option") {
      const label = text(child.props.children);
      const value = child.props.value !== undefined ? String(child.props.value) : label;
      out.push({ value, label, disabled: Boolean(child.props.disabled) });
    }
  });
  walk(children);
  return out;
}

const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

export default function Select({
  value, defaultValue, onChange, children, className = "", disabled = false, id, name, title, placeholder,
  "aria-label": ariaLabel, "aria-labelledby": ariaLabelledby, "aria-describedby": ariaDescribedby, required, autoFocus,
}) {
  const options = useMemo(() => optionsOf(children), [children]);
  const [inner, setInner] = useState(defaultValue !== undefined ? String(defaultValue) : undefined);
  const current = value !== undefined && value !== null ? String(value) : inner ?? options[0]?.value ?? "";
  const selected = options.find((o) => o.value === current) || (value === undefined ? options[0] : null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [pos, setPos] = useState(null);
  const button = useRef(null);
  const layer = useRef(null);
  const search = useRef(null);
  const listId = useId();

  const shown = useMemo(() => {
    const q = norm(query.trim());
    return q ? options.filter((o) => norm(o.label).includes(q)) : options;
  }, [options, query]);

  const place = useCallback(() => {
    const r = button.current?.getBoundingClientRect();
    if (!r) return;
    const height = Math.min(340, 56 + options.length * 38);
    const below = window.innerHeight - r.bottom;
    const up = below < height + 12 && r.top > below;
    setPos({
      left: Math.max(8, Math.min(r.left, window.innerWidth - Math.max(r.width, 240) - 8)),
      width: Math.max(r.width, 240),
      ...(up ? { bottom: window.innerHeight - r.top + 4 } : { top: r.bottom + 4 }),
      maxHeight: Math.max(160, (up ? r.top : below) - 16),
    });
  }, [options.length]);

  const openList = () => {
    if (disabled) return;
    setQuery("");
    const i = options.findIndex((o) => o.value === current);
    setActive(Math.max(0, i));
    place();
    setOpen(true);
  };
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };
  const choose = (o) => {
    if (!o || o.disabled) return;
    close();
    if (o.value === current) return;
    if (value === undefined) setInner(o.value);
    const target = { value: o.value, name, id };
    onChange?.({ target, currentTarget: target, preventDefault() {}, stopPropagation() {} });
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

  // Keep the highlighted option in view.
  useEffect(() => {
    if (open) layer.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const step = (from, dir) => {
    for (let i = from + dir; i >= 0 && i < shown.length; i += dir) if (!shown[i].disabled) return i;
    return from;
  };
  const onListKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => step(a, 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => step(a, -1)); }
    else if (e.key === "Home") { e.preventDefault(); setActive(step(-1, 1)); }
    else if (e.key === "End") { e.preventDefault(); setActive(step(shown.length, -1)); }
    else if (e.key === "Enter") { e.preventDefault(); choose(shown[active]); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "Tab") close(false);
  };
  const onButtonKey = (e) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); openList(); }
  };

  return (
    <>
      <button
        ref={button}
        type="button"
        id={id}
        name={name}
        className={`${className} ss-trigger`.trim()}
        disabled={disabled}
        title={title}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledby}
        aria-describedby={ariaDescribedby}
        aria-required={required || undefined}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        autoFocus={autoFocus}
        onClick={() => (open ? close() : openList())}
        onKeyDown={onButtonKey}
      >
        <span className={`ss-value${selected && selected.value !== "" ? "" : " is-placeholder"}`}>
          {selected ? selected.label : placeholder || "Choose"}
        </span>
      </button>
      {open && pos && createPortal(
        <div ref={layer} className="ss-layer" style={{ left: pos.left, width: pos.width, top: pos.top, bottom: pos.bottom, maxHeight: pos.maxHeight }}
          onKeyDown={onListKey}>
          <div className="ss-search">
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
              <path d="M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm9 3-4.3-4.3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            <input ref={search} type="search" value={query} placeholder="Search…" aria-label={`Search ${ariaLabel || "options"}`}
              aria-controls={listId} aria-activedescendant={shown[active] ? `${listId}-${active}` : undefined}
              onChange={(e) => { setQuery(e.target.value); setActive(0); }} />
          </div>
          <ul id={listId} role="listbox" className="ss-list" aria-label={ariaLabel}>
            {shown.map((o, i) => (
              <li key={`${o.value}-${i}`} id={`${listId}-${i}`} data-index={i} role="option" aria-selected={o.value === current}
                aria-disabled={o.disabled || undefined}
                className={`ss-option${i === active ? " is-active" : ""}${o.value === current ? " is-selected" : ""}${o.disabled ? " is-disabled" : ""}`}
                onMouseEnter={() => !o.disabled && setActive(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(o)}>
                {o.label || " "}
              </li>
            ))}
            {!shown.length && <li className="ss-empty">No match for “{query}”</li>}
          </ul>
        </div>,
        document.body,
      )}
    </>
  );
}
