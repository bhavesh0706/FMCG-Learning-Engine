/*
  Phone cards for list tables (see responsive.css, ".data-table.mobile-cards").

  On phones each row of a table marked "mobile-cards" is shown as a card, with
  every value labelled by its column name. The labels come from the table's own
  header: this copies each <th> text onto the cells of that column as
  data-label, and keeps doing so when rows are added, removed or re-rendered.
  Nothing else is changed: rows, clicks and actions are the same elements.
*/

const SELECTOR = "table.mobile-cards";

function labelTable(table) {
  const heads = [...table.querySelectorAll("thead th")].map((th) => {
    const visible = th.querySelector(".visually-hidden") ? "" : th.innerText.trim();
    return visible.replace(/\s+/g, " ");
  });
  if (!heads.length) return;
  for (const row of table.tBodies[0]?.rows || []) {
    for (const cell of row.cells) {
      const label = heads[cell.cellIndex] || "";
      if (cell.getAttribute("data-label") !== label) cell.setAttribute("data-label", label);
    }
  }
}

function labelAll(root = document) {
  root.querySelectorAll(SELECTOR).forEach(labelTable);
}

let installed = false;

export function installTableLabels() {
  if (installed || typeof window === "undefined" || !("MutationObserver" in window)) return;
  installed = true;
  let queued = false;
  const run = () => {
    queued = false;
    labelAll();
  };
  new MutationObserver(() => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(run);
    }
  }).observe(document.body, { childList: true, subtree: true });
  run();
}
