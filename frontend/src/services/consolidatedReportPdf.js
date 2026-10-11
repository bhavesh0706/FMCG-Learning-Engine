import { jsPDF } from "jspdf";

/*
  Consolidated report: the client summary (A, B, C …) and a downloadable PDF.

  buildClientSections() is the ONE place that decides which client-summary
  sections exist and what they contain. The screen (ClientSummary in App.jsx)
  and the PDF both use it, so the downloaded file always matches the page.
*/

/* ================================================================
   Narrative normalization (display only; saved reports are never changed)

   The saved report narrative (backend: services/consolidation.py) has these shapes:
     open_answers           {"<question_id>": {answered, summary, points:[{point, customers}],
                             translations:[...], error?}}  (an OBJECT keyed by question id)
     common_positives,
     recurring_issues       [{point, customers}]
     neutral_feedback,
     suggestions,
     recommended_actions    ["text"]
     recipe_recommendations [{change, ingredient?, step?, reason?, customers}]
     conclusion             "text"
   Older reports or AI replies may differ (a list instead of the object, plain strings
   instead of {point}, a JSON string, null). normalizeNarrative() accepts all of these
   and keeps every piece of feedback, so the page never calls .map/.find on a non-array.
   ================================================================ */

function parseMaybeJson(v) {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if ((t.startsWith("[") && t.endsWith("]")) || (t.startsWith("{") && t.endsWith("}"))) {
    try {
      return JSON.parse(t);
    } catch {
      return v; // not JSON after all: keep the text
    }
  }
  return v;
}

// Any value -> array: arrays as-is, objects -> their values, a non-empty string -> [string].
export function asList(v) {
  const x = parseMaybeJson(v);
  if (Array.isArray(x)) return x;
  if (x == null || x === "") return [];
  if (typeof x === "object") return Object.values(x);
  return [x];
}

const textOf = (x) =>
  typeof x === "string" || typeof x === "number"
    ? String(x).trim()
    : x && typeof x === "object"
    ? String(x.point ?? x.text ?? x.action ?? x.suggestion ?? x.theme ?? x.summary ?? "").trim()
    : "";

const countOf = (x) => {
  const n = x && typeof x === "object" ? Number(x.customers ?? x.count) : NaN;
  return Number.isFinite(n) ? n : null; // null = count unknown (e.g. the point was plain text)
};

// [{point, customers}] from objects or plain strings; empty points are the only thing skipped.
export function asPoints(v) {
  return asList(v)
    .map((x) => ({ ...(x && typeof x === "object" ? x : {}), point: textOf(x), customers: countOf(x) }))
    .filter((x) => x.point);
}

export const asStrings = (v) => asList(v).map(textOf).filter(Boolean);

function asOpenAnswerEntry(x) {
  const e = x && typeof x === "object" && !Array.isArray(x) ? x : { summary: textOf(x) };
  return {
    ...e,
    summary: typeof e.summary === "string" ? e.summary : textOf(e.summary),
    points: asPoints(e.points),
    translations: asList(e.translations),
  };
}

// open_answers -> {"<question_id>": entry}, whatever shape it was saved in.
export function openAnswersById(v) {
  const x = parseMaybeJson(v);
  const out = {};
  if (Array.isArray(x)) {
    x.forEach((item, i) => {
      const id = item && typeof item === "object" ? item.question_id ?? item.id : undefined;
      out[id != null && id !== "" ? String(id) : `unmatched-${i}`] = asOpenAnswerEntry(item);
    });
  } else if (x && typeof x === "object") {
    Object.entries(x).forEach(([k, item]) => {
      out[String(k)] = asOpenAnswerEntry(item);
    });
  }
  return out;
}

export function normalizeNarrative(raw) {
  const nv = parseMaybeJson(raw);
  if (!nv || typeof nv !== "object" || Array.isArray(nv)) return { open_answers: {} };
  const conclusion = Array.isArray(nv.conclusion) ? asStrings(nv.conclusion).join(" ") : textOf(nv.conclusion);
  return {
    ...nv, // every other field is passed through unchanged
    conclusion,
    common_positives: asPoints(nv.common_positives),
    recurring_issues: asPoints(nv.recurring_issues),
    neutral_feedback: asStrings(nv.neutral_feedback),
    suggestions: asStrings(nv.suggestions),
    recommended_actions: asStrings(nv.recommended_actions),
    themes: asList(nv.themes).filter((t) => t && typeof t === "object"),
    recipe_recommendations: asList(nv.recipe_recommendations)
      .map((r) => (r && typeof r === "object" ? r : { change: textOf(r) }))
      .filter((r) => textOf(r.change ?? r)),
    open_answers: openAnswersById(nv.open_answers),
  };
}

const pctOf = (count, total) => Math.round((100 * count) / Math.max(1, total));

export function clientHeadline(stats, n, narrative = {}) {
  const cs = stats.client_summary || {};
  narrative = normalizeNarrative(narrative);
  const pref = cs.preference;
  const intent = cs.purchase_intent;
  if (pref) {
    return `Out of ${cs.households} households surveyed, ${pref.preferred_pct}% prefer ${pref.this_product}${
      intent?.positive_pct != null ? ` and ${intent.positive_pct}% would buy it` : ""
    }.`;
  }
  // Mid-sentence: "Too sweet" -> "too sweet" (acronyms like "MRP" stay as they are).
  const inline = (t) => (t && /^[A-Z][a-z]/.test(t) ? t[0].toLowerCase() + t.slice(1) : t);
  const liked = inline(narrative?.common_positives?.[0]?.point);
  const concern = inline(narrative?.recurring_issues?.[0]?.point);
  const parts = [liked && `they like ${liked}`, concern && `the main concern is ${concern}`].filter(Boolean);
  return `${n} ${n === 1 ? "customer" : "customers"} surveyed${parts.length ? `: ${parts.join("; ")}` : ""}.`;
}

/*
  Each section: { title, sub?, table?: { head, rows: [{ cells, highlight?, total? }] },
                  points?: [{ text, meta, tone }], text?, list?, note? }
*/
export function buildClientSections(stats, narrative, n) {
  const cs = stats.client_summary || {};
  const nv = normalizeNarrative(narrative);
  const out = [];
  const { preference: pref, price, score, improvement: imp, purchase_intent: intent, ease } = cs;

  if (pref) {
    const others = pref.options.filter((o) => o !== pref.this_product);
    const pctFor = (rows, o) => `${rows.find((x) => x.option === o)?.pct ?? 0}%`;
    out.push({
      title: "Product preference",
      sub: pref.question,
      table: {
        head: ["Location", ...pref.options, "Households"],
        rows: [
          ...pref.by_location.map((r) => ({ cells: [r.location, ...pref.options.map((o) => pctFor(r.rows, o)), String(r.households)] })),
          { total: true, cells: ["Overall preference", ...pref.options.map((o) => pctFor(pref.overall, o)), String(pref.answered)] },
        ],
      },
      note: `${pref.preferred_pct}% of ${pref.answered} households prefer ${pref.this_product}${others.length ? ` over ${others.join(" / ")}` : ""}.`,
    });
  }
  if ((nv.common_positives || []).length) {
    out.push({
      title: "Why customers like it",
      points: nv.common_positives.map((p) => ({
        text: p.point,
        meta: p.customers != null ? `${p.customers} of ${n} customers (${pctOf(p.customers, n)}%)` : "",
        tone: "positive",
      })),
    });
  }
  if (price) {
    out.push({
      title: "Price willingness",
      sub: price.question,
      table: {
        head: ["Price point", "Customers", "% rate"],
        rows: [
          ...price.rows.map((r) => ({ highlight: r.option === price.ideal, cells: [r.option, String(r.count), `${r.pct}%`] })),
          { total: true, cells: ["Customers", String(price.answered), "100%"] },
        ],
      },
      note: price.ideal ? `Ideal price point: ${price.ideal}. ${price.ideal_pct}% of customers would buy at this price.` : null,
    });
  }
  if (score) {
    out.push({
      title: "Overall score",
      sub: score.question,
      table: {
        head: ["Score", "Customers", "% rate"],
        rows: [
          ...[...score.rows].reverse().map((r) => ({ cells: [score.scale ? `${r.option} star${r.option === "1" ? "" : "s"}` : r.option, String(r.count), `${r.pct}%`] })),
          { total: true, cells: ["Customers", String(score.answered), "100%"] },
        ],
      },
      note: `${score.top_two_pct}% gave the top scores (${score.top_two_labels.join(" or ")})${
        score.average != null ? `; average ${score.average}${score.scale ? " out of 5" : ""}` : ""
      }.`,
    });
  }
  if (intent) {
    out.push({
      title: "Purchase intent",
      sub: intent.question,
      table: { head: ["Answer", "Customers", "% rate"], rows: intent.rows.map((r) => ({ cells: [r.option, String(r.count), `${r.pct}%`] })) },
      note: intent.positive_pct != null ? `${intent.positive_pct}% would buy it.` : null,
    });
  }
  if (imp) {
    const rows = imp.from_text
      ? (nv.open_answers[String(imp.question_id)]?.points || []).map((p) => ({
          option: p.point,
          count: p.customers, // null = not counted (older reports): shown as "—", not as 0
          pct: p.customers != null ? pctOf(p.customers, imp.answered) : null,
        }))
      : imp.rows;
    out.push({
      title: "Areas of improvement",
      sub: imp.question,
      table: rows.length
        ? {
            head: ["Improvement area", "Customers", "% rate"],
            rows: rows.map((r, i) => ({
              highlight: i === 0 && r.count > 0,
              cells: [r.option, r.count != null ? String(r.count) : "—", r.pct != null ? `${r.pct}%` : "—"],
            })),
          }
        : null,
      text: rows.length ? null : "Grouped improvement points appear once the report wording has been generated.",
      note: rows[0]?.count ? `Top request: ${rows[0].option} (${rows[0].pct}% of customers).` : null,
    });
  }
  if (ease) {
    out.push({
      title: "Ease of use / preparation",
      sub: ease.question,
      table: { head: ["Answer", "Customers", "% rate"], rows: ease.rows.map((r) => ({ cells: [r.option, String(r.count), `${r.pct}%`] })) },
    });
  }
  if ((nv.recurring_issues || []).length) {
    out.push({
      title: "Product weaknesses",
      points: nv.recurring_issues.map((p) => ({
        text: p.point,
        meta: p.customers != null ? `${p.customers} of ${n} customers` : "",
        tone: "concern",
      })),
    });
  }
  if (nv.conclusion || (nv.recommended_actions || []).length || (nv.suggestions || []).length) {
    out.push({
      title: "Observations and recommendations",
      text: nv.conclusion || null,
      list: nv.recommended_actions || [],
      suggestions: nv.suggestions || [],
    });
  }
  if ((nv.recipe_recommendations || []).length) {
    out.push({
      title: "Recipe recommendations",
      recipe: nv.recipe_recommendations.map((r) => ({
        change: r.change,
        where: [r.step, r.ingredient].filter(Boolean).join(" · "),
        meta: Number.isFinite(Number(r.customers)) && r.customers != null ? `${r.customers} of ${n} customers` : "",
        reason: r.reason || "",
      })),
      note: "Test changes in small batches with your food technologist before changing production.",
    });
  }
  return out;
}

/* ================================================================
   PDF
   ================================================================ */

const C = {
  ink: [16, 32, 48],
  ink2: [52, 68, 84],
  muted: [96, 110, 124],
  line: [214, 223, 232],
  bg: [242, 246, 249],
  brand: [10, 65, 116],
  brandSoft: [228, 239, 247],
  pos: [30, 138, 90],
  posSoft: [228, 243, 235],
  act: [168, 106, 18],
  actSoft: [251, 241, 225],
  neg: [200, 65, 79],
};

// Built-in PDF fonts only cover Latin-1, so swap typographic characters for safe ones.
const clean = (s) =>
  String(s ?? "")
    .replace(/[\u2013\u2014\u2212]/g, "-")
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201C\u201D]/g, '"')
    .replace(/\u2026/g, "...")
    .replace(/[\u2022\u25CF]/g, "-")
    .replace(/\u2605/g, "*")
    .replace(/[\u20B9]/g, "Rs ")
    .replace(/[^\x09\x0A\x0D\x20-\x7E\xA0-\xFF]/g, "");

export function downloadConsolidatedPdf({ report, product, fileName }) {
  const stats = report.stats || {};
  const nv = normalizeNarrative(report.narrative);
  const n = stats.response_count || report.response_count || 0;

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
  const lh = (size) => size * 1.4;
  const newPage = () => {
    doc.addPage();
    y = 50;
  };
  const ensure = (h) => {
    if (y + h > BOTTOM) newPage();
  };
  const para = (text, size = 10.5, style = "normal", color = C.ink2, x = M, width = CW) => {
    font(size, style, color);
    const lines = doc.splitTextToSize(clean(text), width);
    lines.forEach((ln) => {
      ensure(lh(size));
      doc.text(ln, x, y + size);
      y += lh(size);
    });
  };
  const heading = (text, size = 15) => {
    ensure(lh(size) + 24);
    y += 8;
    font(size, "bold", C.ink);
    doc.text(clean(text), M, y + size);
    y += lh(size) + 4;
  };

  // ---- cover band ----
  doc.setFillColor(...C.brand);
  doc.rect(0, 0, W, 112, "F");
  font(10, "bold", [190, 214, 236]);
  doc.text("CONSOLIDATED FEEDBACK REPORT", M, 38);
  font(22, "bold", [255, 255, 255]);
  doc.text(clean(product?.name || "Product"), M, 66);
  font(10, "normal", [214, 228, 241]);
  const generated = report.generated_at ? new Date(String(report.generated_at).endsWith("Z") ? report.generated_at : `${report.generated_at}Z`) : new Date();
  doc.text(
    clean(
      [product?.category_name, product?.sku, `Based on ${n} ${n === 1 ? "survey" : "surveys"}`, `Updated ${generated.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`]
        .filter(Boolean)
        .join("  -  ")
    ),
    M,
    88
  );
  y = 132;

  // ---- headline ----
  const headline = clean(clientHeadline(stats, n, nv));
  font(13, "bold", C.brand);
  const hl = doc.splitTextToSize(headline, CW - 28);
  const hh = hl.length * lh(13) + 22;
  doc.setFillColor(...C.brandSoft);
  doc.roundedRect(M, y, CW, hh, 8, 8, "F");
  hl.forEach((ln, i) => doc.text(ln, M + 14, y + 16 + 13 + i * lh(13) - 4));
  y += hh + 10;

  // ---- client summary ----
  heading("Client summary", 16);
  const sections = buildClientSections(stats, nv, n);
  sections.forEach((sec, i) => {
    ensure(70);
    // letter badge + title
    doc.setFillColor(...C.brand);
    doc.roundedRect(M, y, 20, 20, 4, 4, "F");
    font(11, "bold", [255, 255, 255]);
    doc.text(String.fromCharCode(65 + i), M + 10, y + 14, { align: "center" });
    font(13, "bold", C.ink);
    doc.text(clean(sec.title), M + 28, y + 14.5);
    y += 26;
    if (sec.sub) para(sec.sub, 9.5, "normal", C.muted, M + 28, CW - 28);
    y += 2;

    if (sec.table) {
      const cols = sec.table.head.length;
      const first = Math.min(CW * 0.42, 220);
      const rest = (CW - first) / Math.max(1, cols - 1);
      const colX = (c) => (c === 0 ? M + 8 : M + first + rest * (c - 1) + rest - 8);
      const rowH = 18;
      // Long column names (e.g. "Vendor's Hausa koko") wrap inside their own column.
      font(8.5, "bold", C.muted);
      const headLines = sec.table.head.map((h, c) => doc.splitTextToSize(clean(h).toUpperCase(), (c === 0 ? first : rest) - 12));
      const headH = Math.max(...headLines.map((l) => l.length)) * 10 + 8;
      ensure(headH + rowH);
      doc.setFillColor(...C.bg);
      doc.rect(M, y, CW, headH, "F");
      font(8.5, "bold", C.muted);
      headLines.forEach((lines, c) =>
        lines.forEach((ln, k) => doc.text(ln, colX(c), y + 12 + k * 10, { align: c === 0 ? "left" : "right" }))
      );
      y += headH;
      sec.table.rows.forEach((row) => {
        ensure(rowH);
        if (row.highlight) {
          doc.setFillColor(...C.brandSoft);
          doc.rect(M, y, CW, rowH, "F");
        }
        if (row.total) {
          doc.setDrawColor(...C.ink2);
          doc.setLineWidth(0.8);
          doc.line(M, y, M + CW, y);
        }
        font(9.5, row.highlight || row.total ? "bold" : "normal", C.ink);
        row.cells.forEach((cell, c) => {
          const txt = clean(cell);
          const fitted = c === 0 ? doc.splitTextToSize(txt, first - 14)[0] : txt;
          doc.text(fitted, colX(c), y + 12.5, { align: c === 0 ? "left" : "right" });
        });
        doc.setDrawColor(...C.line);
        doc.setLineWidth(0.5);
        doc.line(M, y + rowH, M + CW, y + rowH);
        y += rowH;
      });
      y += 4;
    }

    (sec.points || []).forEach((p) => {
      font(10, "bold", C.ink);
      const lines = doc.splitTextToSize(clean(p.text), CW - 170);
      const h = Math.max(20, lines.length * lh(10) + 8);
      ensure(h + 4);
      doc.setFillColor(...(p.tone === "concern" ? C.actSoft : C.posSoft));
      doc.roundedRect(M, y, CW, h, 5, 5, "F");
      lines.forEach((ln, k) => doc.text(ln, M + 10, y + 13.5 + k * lh(10)));
      font(9, "normal", C.muted);
      doc.text(clean(p.meta), M + CW - 10, y + 13.5, { align: "right" });
      y += h + 4;
    });

    if (sec.text) para(sec.text, 10.5, "normal", C.ink2);
    if ((sec.list || []).length) {
      para("Recommended actions", 10, "bold", C.ink);
      sec.list.forEach((item) => para(`-  ${item}`, 10.5, "normal", C.ink2, M + 6, CW - 6));
    }
    if ((sec.suggestions || []).length) {
      para("Customers suggested", 10, "bold", C.ink);
      sec.suggestions.forEach((item) => para(`-  ${item}`, 10.5, "normal", C.ink2, M + 6, CW - 6));
    }
    (sec.recipe || []).forEach((r) => {
      para(`${r.where ? `${r.where}: ` : ""}${r.change}`, 10.5, "bold", C.ink);
      para(`${r.meta}${r.reason ? ` - because: ${r.reason}` : ""}`, 9.5, "normal", C.muted);
      y += 3;
    });

    if (sec.note) {
      font(10, "bold", C.ink);
      const lines = doc.splitTextToSize(clean(sec.note), CW - 22);
      const h = lines.length * lh(10) + 10;
      ensure(h + 6);
      doc.setFillColor(...C.bg);
      doc.rect(M, y + 2, CW, h, "F");
      doc.setFillColor(...C.brand);
      doc.rect(M, y + 2, 3, h, "F");
      lines.forEach((ln, k) => doc.text(ln, M + 12, y + 2 + 14 + k * lh(10) - 2));
      y += h + 6;
    }
    y += 12;
  });

  // ---- detailed insights ----
  newPage();
  heading("Detailed insights", 16);
  if (stats.overall_rating != null) {
    para(`Overall product rating: ${Number(stats.overall_rating).toFixed(1)} out of 5 (average of ${n} customers${stats.median_rating != null ? `, median ${Number(stats.median_rating).toFixed(1)}` : ""}).`, 11, "bold", C.ink);
    const rd = stats.rating_distribution || {};
    para(["5", "4", "3", "2", "1"].map((k) => `${k} stars: ${rd[k] || 0}`).join("    "), 10, "normal", C.muted);
  }
  const sd = stats.sentiment_distribution || {};
  para(`Sentiment: ${sd.positive || 0} positive, ${sd.mixed || 0} mixed, ${sd.negative || 0} negative${sd.neutral ? `, ${sd.neutral} neutral` : ""}.`, 10.5);
  y += 6;

  const aspects = (stats.aspects || []).slice(0, 15);
  if (aspects.length) {
    heading("Feedback themes", 13);
    const head = ["Theme", "Customers", "Positive", "Concerns", "Avg rating"];
    const first = CW * 0.4;
    const rest = (CW - first) / 4;
    const colX = (c) => (c === 0 ? M + 8 : M + first + rest * (c - 1) + rest - 8);
    doc.setFillColor(...C.bg);
    doc.rect(M, y, CW, 18, "F");
    font(8.5, "bold", C.muted);
    head.forEach((h, c) => doc.text(h.toUpperCase(), colX(c), y + 12, { align: c === 0 ? "left" : "right" }));
    y += 18;
    aspects.forEach((a) => {
      ensure(18);
      font(9.5, "normal", C.ink);
      [a.name, a.customers, a.positive, a.negative, a.avg_rating != null ? a.avg_rating.toFixed(1) : "-"].forEach((v, c) =>
        doc.text(c === 0 ? doc.splitTextToSize(clean(v), first - 14)[0] : clean(v), colX(c), y + 12.5, { align: c === 0 ? "left" : "right" })
      );
      doc.setDrawColor(...C.line);
      doc.line(M, y + 18, M + CW, y + 18);
      y += 18;
    });
    y += 6;
    const quoted = aspects.filter((a) => (a.evidence || []).length).slice(0, 6);
    if (quoted.length) {
      heading("What customers said", 13);
      quoted.forEach((a) => {
        para(a.name, 10, "bold", C.ink);
        a.evidence.slice(0, 2).forEach((q) => para(`"${q}"`, 9.5, "italic", C.ink2, M + 10, CW - 10));
        y += 4;
      });
    }
  }

  const qs = (stats.questions || []).filter((q) => q.distribution && Object.keys(q.distribution).length);
  if (qs.length) {
    heading("Survey answers", 13);
    qs.forEach((q) => {
      para(q.question, 10, "bold", C.ink);
      const total = Object.values(q.distribution).reduce((a, b) => a + b, 0);
      para(
        Object.entries(q.distribution).map(([k, v]) => `${q.type === "rating" ? `${k} stars` : k}: ${v} (${pctOf(v, total)}%)`).join("   "),
        9.5, "normal", C.ink2
      );
      y += 3;
    });
  }

  const bd = stats.breakdowns || {};
  const groups = [["gender", "By gender"], ["age_group", "By age"], ["city", "By city"], ["usage_period", "By usage period"]].filter(([k]) => (bd[k] || []).length);
  if (groups.length) {
    heading("Breakdowns", 13);
    groups.forEach(([k, label]) => {
      para(label, 10, "bold", C.ink);
      bd[k].forEach((g) =>
        para(
          `${g.group}: ${g.responses} ${g.responses === 1 ? "response" : "responses"}${g.avg_rating != null ? `, avg ${g.avg_rating.toFixed(1)}` : ""} (${g.positive} positive, ${g.mixed} mixed, ${g.negative} negative)`,
          9.5, "normal", C.ink2, M + 10, CW - 10
        )
      );
      y += 3;
    });
  }

  // ---- footer on every page ----
  const pages = doc.getNumberOfPages();
  for (let p = 1; p <= pages; p++) {
    doc.setPage(p);
    doc.setDrawColor(...C.line);
    doc.line(M, H - 36, W - M, H - 36);
    font(8.5, "normal", C.muted);
    doc.text(clean(`${product?.name || "Product"} - consolidated feedback report`), M, H - 22);
    doc.text(`Page ${p} of ${pages}`, W - M, H - 22, { align: "right" });
  }

  doc.save(fileName || `${clean(product?.name || "product").replace(/[^\w-]+/g, "-")}-consolidated-report.pdf`);
}