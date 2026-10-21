import { useEffect, useId, useRef, useState } from "react";

/*
  In-app confirmation dialog, used instead of window.confirm(), which shows the
  browser's own "localhost:5173 says" box at the top of the page.

  Usage anywhere in the app:
      const ok = await confirmDialog({
        title: "Delete this recording?",
        message: "It can't be recovered.",
        confirmLabel: "Delete",
        danger: true,
      });

  <ConfirmHost /> must be rendered once (App.jsx does this).
*/

let openRequest = null; // set while <ConfirmHost /> is mounted

export function confirmDialog(options = {}) {
  if (!openRequest) {
    // Host not mounted (shouldn't happen): fall back to the browser dialog.
    return Promise.resolve(window.confirm(options.message || options.title || "Are you sure?"));
  }
  return new Promise((resolve) => openRequest({ ...options, resolve }));
}

export function ConfirmHost() {
  const [request, setRequest] = useState(null);

  useEffect(() => {
    openRequest = (r) => setRequest(r);
    return () => {
      openRequest = null;
    };
  }, []);

  if (!request) return null;

  const close = (answer) => {
    request.resolve(answer);
    setRequest(null);
  };

  return <ConfirmDialogView {...request} onClose={close} />;
}

function ConfirmDialogView({
  title = "Are you sure?",
  message = "",
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
  onClose,
}) {
  const titleId = useId();
  const messageId = useId();
  const cancelRef = useRef(null);
  const confirmRef = useRef(null);

  // Destructive actions start on "Cancel", so pressing Enter can't delete by accident.
  const panelRef = useFocusTrap({ initialFocus: danger ? cancelRef : confirmRef });

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="dialog-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose(false)}>
      <div
        ref={panelRef}
        className={`dialog${danger ? " is-danger" : ""}`}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={message ? messageId : undefined}
      >
        <span className="dialog-icon" aria-hidden="true">
          {danger ? (
            <svg viewBox="0 0 24 24" width="20" height="20">
              <path d="M4 7h16M9 7V5h6v2M7 7l1 12h8l1-12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="20" height="20">
              <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
              <path d="M12 8v5M12 16.5v.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          )}
        </span>
        <h2 id={titleId} className="dialog-title">{title}</h2>
        {message && (
          <p id={messageId} className="dialog-message">
            {message}
          </p>
        )}
        <div className="dialog-actions">
          <button ref={cancelRef} type="button" className="btn btn-secondary" onClick={() => onClose(false)}>
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={`btn ${danger ? "btn-danger" : "btn-primary"}`}
            onClick={() => onClose(true)}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/*
  Keeps keyboard focus inside a modal while it's open (Tab / Shift+Tab cycle
  through its buttons), focuses it when it opens, and returns focus to the
  element that opened it when it closes. Returns a ref for the modal element.
*/
export function useFocusTrap({ initialFocus, lockScroll = true } = {}) {
  const ref = useRef(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const previouslyFocused = document.activeElement;
    const selector =
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    const first = initialFocus?.current || node.querySelector(selector);
    if (first) first.focus();
    else {
      node.setAttribute("tabindex", "-1");
      node.focus();
    }

    const onKey = (e) => {
      if (e.key !== "Tab") return;
      const items = Array.from(node.querySelectorAll(selector));
      if (!items.length) return;
      const firstItem = items[0];
      const lastItem = items[items.length - 1];
      if (e.shiftKey && document.activeElement === firstItem) {
        e.preventDefault();
        lastItem.focus();
      } else if (!e.shiftKey && document.activeElement === lastItem) {
        e.preventDefault();
        firstItem.focus();
      }
    };
    node.addEventListener("keydown", onKey);

    const prevOverflow = document.body.style.overflow;
    if (lockScroll) document.body.style.overflow = "hidden";

    return () => {
      node.removeEventListener("keydown", onKey);
      if (lockScroll) document.body.style.overflow = prevOverflow;
      if (previouslyFocused && typeof previouslyFocused.focus === "function") previouslyFocused.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return ref;
}

/* A <div> that traps focus while mounted; for modals rendered inline. */
export function TrapFocus({ children, lockScroll = false, ...props }) {
  const ref = useFocusTrap({ lockScroll });
  return (
    <div ref={ref} {...props}>
      {children}
    </div>
  );
}