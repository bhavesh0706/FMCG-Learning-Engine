import { useEffect } from "react";
import { TrapFocus } from "./ConfirmDialog";

/* Compact dialog used by Catalog and Team: title, body, footer buttons.
   Escape or a click outside closes it; the first field gets focus. */
export default function Modal({ title, subtitle, onClose, children, footer }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  // Start typing straight away: focus the first field (after the focus trap sets up).
  useEffect(() => {
    const t = setTimeout(() => {
      const first = document.querySelector(".modal-body input:not([type=checkbox]), .modal-body select, .modal-body textarea");
      if (first) first.focus();
    }, 30);
    return () => clearTimeout(t);
  }, []);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <TrapFocus className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" lockScroll>
        <div className="modal-head">
          <div>
            <h2 id="modal-title">{title}</h2>
            {subtitle && <p className="muted">{subtitle}</p>}
          </div>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">{children}</div>
        <div className="modal-foot">{footer}</div>
      </TrapFocus>
    </div>
  );
}