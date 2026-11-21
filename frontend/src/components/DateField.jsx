import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/*
  A date field with a large, readable calendar (instead of the browser's small native picker).
  Days before `min` can't be chosen (e.g. no start date in the past). Values are "YYYY-MM-DD".

    <DateField value={start} onChange={setStart} min={todayIso()} placeholder="Choose a date" />
*/

const pad = (n) => String(n).padStart(2, "0");
export const toIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const todayIso = () => toIso(new Date());
const fromIso = (s) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
};
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const longDate = (iso) => {
  const d = fromIso(iso);
  return d ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" }) : "";
};

export default function DateField({ value, onChange, min, id, placeholder = "Choose a date", ariaLabel, disabled = false, clearable = true }) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState(() => fromIso(value) || fromIso(min) || new Date());
  const [pos, setPos] = useState(null);
  const triggerRef = useRef(null);
  const popRef = useRef(null);
  const gridId = useId();

  useEffect(() => {
    if (open) setView(fromIso(value) || fromIso(min) || new Date());
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Place the calendar under the field (or above it when there's no room), on top of dialogs.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return undefined;
    const place = () => {
      const r = triggerRef.current.getBoundingClientRect();
      // As wide as the date box (at least 248px so the days stay tappable).
      const width = Math.min(Math.max(r.width, 248), window.innerWidth - 24);
      const height = 300;
      const below = window.innerHeight - r.bottom;
      const top = below >= height + 8 || below >= r.top ? r.bottom + 6 : Math.max(8, r.top - height - 6);
      const left = Math.min(Math.max(12, r.left), window.innerWidth - width - 12);
      setPos({ top, left, width });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (!popRef.current?.contains(e.target) && !triggerRef.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  const minDate = fromIso(min);
  const year = view.getFullYear();
  const month = view.getMonth();
  const first = new Date(year, month, 1);
  const lead = (first.getDay() + 6) % 7; // Monday first
  const days = new Date(year, month + 1, 0).getDate();
  const cells = [...Array(lead).fill(null), ...Array.from({ length: days }, (_, i) => new Date(year, month, i + 1))];
  while (cells.length % 7) cells.push(null);
  const today = todayIso();
  const prevDisabled = minDate && new Date(year, month, 0) < minDate;
  const choose = (d) => {
    onChange(toIso(d));
    setOpen(false);
    triggerRef.current?.focus();
  };
  const monthName = view.toLocaleDateString(undefined, { month: "long", year: "numeric" });

  return (
    <span className="date-field">
      <button ref={triggerRef} id={id} type="button" className={`input date-trigger${value ? "" : " is-empty"}`} disabled={disabled}
        aria-haspopup="dialog" aria-expanded={open} aria-label={ariaLabel ? `${ariaLabel}: ${value ? longDate(value) : "not set"}` : undefined}
        onClick={() => setOpen((o) => !o)}>
        <svg className="date-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <rect x="3.5" y="5" width="17" height="15.5" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.7" />
          <path d="M3.5 9.5h17M8 3v4M16 3v4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
        </svg>
        <span>{value ? longDate(value) : placeholder}</span>
      </button>
      {open && pos && createPortal(
        <div ref={popRef} className="date-pop" role="dialog" aria-label={ariaLabel || "Choose a date"}
          style={{ top: pos.top, left: pos.left, width: pos.width }}>
          <div className="date-pop-head">
            <button type="button" className="date-nav" aria-label="Previous month" disabled={prevDisabled}
              onClick={() => setView(new Date(year, month - 1, 1))}>‹</button>
            <strong aria-live="polite">{monthName}</strong>
            <button type="button" className="date-nav" aria-label="Next month" onClick={() => setView(new Date(year, month + 1, 1))}>›</button>
          </div>
          <div className="date-grid" role="grid" aria-labelledby={gridId}>
            <span id={gridId} hidden>{monthName}</span>
            {WEEKDAYS.map((w) => <span key={w} className="date-wd" role="columnheader">{w}</span>)}
            {cells.map((d, i) => {
              if (!d) return <span key={`x${i}`} className="date-blank" />;
              const iso = toIso(d);
              const past = minDate && d < minDate;
              const cls = ["date-day", iso === value && "is-selected", iso === today && "is-today", past && "is-off"].filter(Boolean).join(" ");
              return (
                <button key={iso} type="button" className={cls} disabled={past} aria-pressed={iso === value}
                  aria-label={`${longDate(iso)}${past ? " (in the past)" : ""}`} onClick={() => choose(d)}>
                  {d.getDate()}
                </button>
              );
            })}
          </div>
          <div className="date-pop-foot">
            <button type="button" className="link-btn" disabled={Boolean(minDate && fromIso(today) < minDate)}
              onClick={() => choose(fromIso(today))}>Today</button>
            {clearable && value && <button type="button" className="link-btn" onClick={() => { onChange(""); setOpen(false); }}>Clear</button>}
            {minDate && <span className="muted small">Earlier dates can't be chosen</span>}
          </div>
        </div>,
        document.body,
      )}
    </span>
  );
}
