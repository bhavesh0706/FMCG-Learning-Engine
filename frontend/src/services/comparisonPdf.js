import { jsPDF } from "jspdf";
import { C, clean as baseClean } from "./consolidatedReportPdf";

// The PDF font has no arrow: "0.8 kg → 0.65 kg" becomes "0.8 kg -> 0.65 kg".
const clean = (s) => baseClean(String(s ?? "").replace(/\u2192/g, "->"));

/*
  Organization portal -> product -> Compare rounds -> "Comparison PDF".
  Same look as the consolidated report PDF: a cover band, the result in one line,
  the key numbers of both rounds, what consumers said, and the recipe changes.
  Input: the response of GET /org/products/{id}/compare.
*/

const pct = (v) => (v == null ? "-" : `${v}%`);
const num = (v) => (v == null ? "-" : Number(v).toFixed(1));

function delta(a, b, unit = "", better = "up") {
  if (a == null || b == null) return { text: "-", tone: null };
  const d = Math.round((b - a) * 10) / 10;
  if (d === 0) return { text: "0", tone: null };
  const good = better === "up" ? d > 0 : d < 0;
  return { text: `${d > 0 ? "+" : ""}${d}${unit}`, tone: good ? C.pos : C.neg };
}

export function downloadComparisonPdf({ product, comparison }) {
  const { a, b } = comparison;
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const W = doc.internal.pageSize.getWidth();
  const H = doc.internal.pageSize.getHeight();
  const M = 44;
  const CW = W - M * 2;
  const BOTTOM = H - 54;
  let y = 0;

  const font = (size, style = "normal", color = C.ink) => {
    doc.setFont("helvetica", style);
    doc.setFontSize(size);
    doc.setTextColor(...color);
  };
  const ensure = (h) => {
    if (y + h > BOTTOM) {
      doc.addPage();
      y = 50;
    }
  };
  const heading = (text) => {
    ensure(40);
    y += 10;
    font(14, "bold", C.ink);
    doc.text(clean(text), M, y + 14);
    y += 26;
  };

  // A table: first column left, the others right-aligned.
  const table = (head, rows) => {
    const first = CW * 0.4;
    const rest = (CW - first) / (head.length - 1);
    const x = (c) => (c === 0 ? M + 8 : M + first + rest * c - 8);
    ensure(20);
    doc.setFillColor(...C.bg);
    doc.rect(M, y, CW, 20, "F");
    font(8.5, "bold", C.muted);
    head.forEach((h, c) => doc.text(clean(h).toUpperCase(), x(c), y + 13, { align: c === 0 ? "left" : "right" }));
    y += 20;
    rows.forEach((row) => {
      ensure(19);
      row.forEach((cell, c) => {
        const value = typeof cell === "object" && cell !== null ? cell : { text: cell };
        font(10, c === 0 ? "normal" : "bold", value.tone || (c === 0 ? C.ink2 : C.ink));
        const text = doc.splitTextToSize(clean(String(value.text ?? "-")), c === 0 ? first - 12 : rest - 8)[0];
        doc.text(text, x(c), y + 13, { align: c === 0 ? "left" : "right" });
      });
      doc.setDrawColor(...C.line);
      doc.line(M, y + 19, M + CW, y + 19);
      y += 19;
    });
    y += 6;
  };

  // ---- cover band ----
  doc.setFillColor(...C.brand);
  doc.rect(0, 0, W, 112, "F");
  font(10, "bold", [190, 214, 236]);
  doc.text("ROUND COMPARISON", M, 38);
  font(22, "bold", [255, 255, 255]);
  doc.text(clean(product?.name || "Product"), M, 66);
  font(10, "normal", [214, 228, 241]);
  doc.text(
    clean(
      [
        product?.category_name,
        `Round ${a.number} (recipe v${a.recipe_version ?? "-"}) vs Round ${b.number} (recipe v${b.recipe_version ?? "-"})`,
        new Date().toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }),
      ]
        .filter(Boolean)
        .join("  -  ")
    ),
    M,
    88
  );
  y = 132;

  // ---- result ----
  if (comparison.headline) {
    font(13, "bold", C.brand);
    const lines = doc.splitTextToSize(clean(comparison.headline), CW - 28);
    const h = lines.length * 18 + 22;
    doc.setFillColor(...C.brandSoft);
    doc.roundedRect(M, y, CW, h, 8, 8, "F");
    lines.forEach((ln, i) => doc.text(ln, M + 14, y + 25 + i * 18));
    y += h + 10;
  }

  // ---- key numbers ----
  const na = a.numbers || {};
  const nb = b.numbers || {};
  heading("Key numbers");
  table(
    ["", `Round ${a.number}`, `Round ${b.number}`, "Change"],
    [
      ["Consumers", na.consumers ?? "-", nb.consumers ?? "-", b.status === "live" ? "so far" : ""],
      ["Average rating (out of 5)", num(na.rating), num(nb.rating), delta(na.rating, nb.rating)],
      ["Positive consumers", pct(na.positive_pct), pct(nb.positive_pct), delta(na.positive_pct, nb.positive_pct, " pts")],
      ["Would buy", pct(na.would_buy_pct), pct(nb.would_buy_pct), delta(na.would_buy_pct, nb.would_buy_pct, " pts")],
      ["Prefer this product", pct(na.preferred_pct), pct(nb.preferred_pct), delta(na.preferred_pct, nb.preferred_pct, " pts")],
      ["Ideal price", na.ideal_price || "-", nb.ideal_price || "-", ""],
    ]
  );

  // ---- what consumers said ----
  if ((comparison.aspects || []).length) {
    heading("What consumers said (share of consumers)");
    table(
      ["Aspect", `Concern R${a.number}`, `Concern R${b.number}`, `Praise R${a.number}`, `Praise R${b.number}`],
      comparison.aspects.map((r) => [
        r.name,
        pct(r.a.concern_pct),
        { text: pct(r.b.concern_pct), tone: delta(r.a.concern_pct, r.b.concern_pct, "", "down").tone },
        pct(r.a.praise_pct),
        { text: pct(r.b.praise_pct), tone: delta(r.a.praise_pct, r.b.praise_pct).tone },
      ])
    );
  }

  // ---- recipe ----
  heading(`Recipe used: v${a.recipe_version ?? "-"} -> v${b.recipe_version ?? "-"}`);
  const changes = comparison.recipe_changes || [];
  font(10.5, "normal", C.ink2);
  if (!changes.length) {
    ensure(16);
    doc.text(comparison.same_recipe ? "Same recipe in both rounds." : "No recipe details to compare.", M, y + 11);
    y += 18;
  } else {
    changes.forEach((c) => {
      const lines = doc.splitTextToSize(`- ${clean(c)}`, CW - 10);
      lines.forEach((ln) => {
        ensure(15);
        doc.text(ln, M + 4, y + 11);
        y += 15;
      });
    });
  }

  // ---- footer on every page ----
  const pages = doc.getNumberOfPages();
  for (let i = 1; i <= pages; i++) {
    doc.setPage(i);
    font(8.5, "normal", C.muted);
    doc.text("Aggregated consumer results only: no personal details of any consumer.", M, H - 28);
    doc.text(`${i} / ${pages}`, W - M, H - 28, { align: "right" });
  }
  const safe = clean(product?.name || "product").replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "");
  doc.save(`${safe}-round-${a.number}-vs-${b.number}.pdf`);
}
