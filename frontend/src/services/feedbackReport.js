import { jsPDF } from "jspdf";

/*
  Builds a customer feedback report as a PDF and downloads it.

  report = {
    filename, dateLabel, dateValue,
    meta: [{ label, value, tone? }],
    sentiment: { tone, label },
    insights: { overview[], positives[], issues[], actions[], notes[] },
    parts: [{ part, range, label, tone, summary, keyPoint }],
    transcript: { type: "timed", lines: [{ time, text }] }
              | { type: "blocks", blocks: [{ label, paragraphs[] }] }
  }
*/

const C = {
  ink: [19, 32, 28],
  ink2: [51, 64, 60],
  muted: [94, 107, 103],
  line: [223, 228, 225],
  bg: [242, 244, 243],
  brand: [11, 93, 82],
  brandSoft: [227, 239, 236],
  pos: [30, 138, 90],
  neg: [200, 65, 79],
  act: [168, 106, 18],
  neutral: [140, 150, 146],
};

const toneColor = (tone) =>
  ({ positive: C.pos, negative: C.neg, mixed: C.act, action: C.act, neutral: C.neutral }[tone] || C.ink);

// Built-in PDF fonts only cover Latin-1, so swap typographic characters for safe ones.
const clean = (s) =>
  String(s ?? "")
    .replace(/[\u2013\u2014\u2212]/g, "-")
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201C\u201D]/g, '"')
    .replace(/\u2026/g, "...")
    .replace(/[\u2022\u25CF]/g, "-")
    // eslint-disable-next-line no-control-regex -- keeps tab/newline, drops characters the PDF font can't draw
    .replace(/[^\x09\x0A\x0D\x20-\x7E\xA0-\xFF]/g, "");

export function downloadFeedbackPdf(report, fileName) {
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const W = doc.internal.pageSize.getWidth();
  const H = doc.internal.pageSize.getHeight();
  const M = 48;
  const CW = W - M * 2;
  const TOP = 54;
  const BOTTOM = H - 58;
  let y = TOP;

  const font = (size, style = "normal", color = C.ink) => {
    doc.setFont("helvetica", style);
    doc.setFontSize(size);
    doc.setTextColor(...color);
  };
  const lh = (size) => size * 1.45;
  const ensure = (h) => {
    if (y + h > BOTTOM) {
      doc.addPage();
      y = TOP;
    }
  };
  const write = (text, x, opts = {}) => doc.text(text, x, y, { baseline: "top", ...opts });

  const paragraph = (text, { size = 10.5, style = "normal", color = C.ink2, indent = 0, gap = 6 } = {}) => {
    const lines = doc.splitTextToSize(clean(text), CW - indent);
    lines.forEach((line) => {
      ensure(lh(size));
      font(size, style, color);
      write(line, M + indent);
      y += lh(size);
    });
    y += gap;
  };

  const section = (title) => {
    ensure(70);
    y += 10;
    font(13, "bold", C.ink);
    write(clean(title), M);
    y += 20;
    doc.setDrawColor(...C.line);
    doc.setLineWidth(0.8);
    doc.line(M, y, M + CW, y);
    y += 12;
  };

  const bulletList = (title, items, tone) => {
    if (!items?.length) return;
    ensure(44);
    font(10.5, "bold", toneColor(tone));
    write(`${clean(title)} (${items.length})`, M);
    y += 18;

    items.forEach((item) => {
      font(10.5, "normal", C.ink2);
      const lines = doc.splitTextToSize(clean(item), CW - 16);
      ensure(lh(10.5) * Math.min(lines.length, 2));
      doc.setFillColor(...toneColor(tone));
      doc.circle(M + 4, y + 6.5, 2.3, "F");
      lines.forEach((line) => {
        ensure(lh(10.5));
        font(10.5, "normal", C.ink2);
        write(line, M + 16);
        y += lh(10.5);
      });
      y += 3;
    });
    y += 8;
  };

const ratingTone = (v) => (v >= 4 ? "positive" : v >= 3 ? "mixed" : "negative");

  // Horizontal score bar with "4.5 / 5" beside it, drawn at the current line.
  const ratingBar = (value, xRight, width = 110) => {
    const v = Math.max(0, Math.min(5, Number(value) || 0));
    const x = xRight - width - 44;
    doc.setFillColor(...C.bg);
    doc.roundedRect(x, y + 4, width, 6, 3, 3, "F");
    doc.setFillColor(...toneColor(ratingTone(v)));
    if (v > 0) doc.roundedRect(x, y + 4, Math.max(6, (v / 5) * width), 6, 3, 3, "F");
    font(9.5, "bold", C.ink);
    write(`${v.toFixed(1)} / 5`, xRight, { align: "right" });
  };

  /* ---------- Header ---------- */

  const generated = new Date().toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  doc.setFillColor(...C.brand);
  doc.rect(0, 0, W, 6, "F");

  font(8.5, "bold", C.brand);
  write("FMCG VIDEO LEARNING ENGINE", M);
  font(8.5, "normal", C.muted);
  write(`Generated ${clean(generated)}`, M + CW, { align: "right" });
  y += 24;

  font(22, "bold", C.ink);
  write("Customer feedback report", M);
  y += 32;
  paragraph(report.filename, { size: 11, color: C.muted, gap: 16 });

  /* ---------- Key facts ---------- */

  const meta = (report.meta || []).filter((m) => m.value);
  if (meta.length) {
    const gapX = 8;
    const bw = (CW - gapX * (meta.length - 1)) / meta.length;
    font(11, "bold");
    const wrapped = meta.map((m) => doc.splitTextToSize(clean(m.value), bw - 20).slice(0, 2));
    const maxLines = Math.max(...wrapped.map((w) => w.length));
    const bh = 34 + maxLines * lh(11);
    ensure(bh);
    meta.forEach((m, i) => {
      const x = M + i * (bw + gapX);
      doc.setFillColor(...C.bg);
      doc.roundedRect(x, y, bw, bh, 6, 6, "F");
      font(8, "normal", C.muted);
      doc.text(clean(m.label), x + 10, y + 10, { baseline: "top" });
      font(11, "bold", m.tone ? toneColor(m.tone) : C.ink);
      doc.text(wrapped[i], x + 10, y + 25, { baseline: "top", lineHeightFactor: 1.45 });
    });
    y += bh + 20;
  }

  /* ---------- Summary ---------- */

  const ins = report.insights || {};
  section("Summary");
  if (ins.overview?.length) {
    ins.overview.forEach((p) => paragraph(p, { size: 11, color: C.ink, gap: 6 }));
    y += 6;
  }
  bulletList("What customers liked", ins.positives, "positive");
  bulletList("Issues raised", ins.issues, "negative");
  bulletList("Recommended actions", ins.actions, "action");
  bulletList("Other notes", ins.notes, "neutral");
  if (!ins.overview?.length && !ins.positives?.length && !ins.issues?.length && !ins.actions?.length) {
    paragraph("No summary was returned for this video.", { color: C.muted });
  }

/* ---------- Ratings by feedback area ---------- */

  const ratedAreas = (report.ratings || []).filter((r) => Number.isFinite(r.rating));
  if (ratedAreas.length) {
    section("Ratings by feedback area");
    ratedAreas.forEach((r) => {
      ensure(28);
      font(10.5, "bold", C.ink);
      write(clean(r.name), M);
      const nw = doc.getTextWidth(clean(r.name));
      font(9, "normal", C.muted);
      write(clean(r.category), M + nw + 8, { baseline: "top" });
      ratingBar(r.rating, M + CW);
      y += 18;
      doc.setDrawColor(...C.line);
      doc.setLineWidth(0.5);
      doc.line(M, y, M + CW, y);
      y += 8;
    });
    y += 6;
  }

  /* ---------- Parts ---------- */

  if (report.parts?.length) {
    section("Parts analyzed");
    report.parts.forEach((p) => {
      ensure(64);
      const title = `Part ${p.part}`;
      font(11, "bold", C.ink);
      write(title, M);
      const tw = doc.getTextWidth(title);
      font(10, "normal", C.muted);
      write(clean(p.range), M + tw + 10, { baseline: "top" });
      if (p.label) {
        font(9, "bold", toneColor(p.tone));
        write(clean(p.label).toUpperCase(), M + CW, { align: "right" });
      }
      y += 19;

      if (p.summary) paragraph(p.summary, { gap: 6 });

      if (p.keyPoint) {
        font(10, "normal", C.ink2);
        const lines = doc.splitTextToSize(clean(p.keyPoint), CW - 28);
        const h = 26 + lines.length * lh(10);
        ensure(h);
        doc.setFillColor(...C.brandSoft);
        doc.rect(M, y, CW, h, "F");
        doc.setFillColor(...C.brand);
        doc.rect(M, y, 3, h, "F");
        font(7.5, "bold", C.brand);
        doc.text("KEY TAKEAWAY", M + 14, y + 8, { baseline: "top" });
        font(10, "normal", C.ink2);
        doc.text(lines, M + 14, y + 20, { baseline: "top", lineHeightFactor: 1.45 });
        y += h;
      }

      const fb = (p.feedback || []).filter((f) => f.name);
      if (fb.length) {
        y += 10;
        ensure(80); // keep the heading with its first item
        font(8, "bold", C.muted);
        write("CUSTOMER FEEDBACK", M);
        y += 14;
        fb.forEach((f) => {
          ensure(34);
          font(10, "bold", C.ink);
          write(clean(f.name), M + 10);
          const tail = [f.category, f.sentiment, f.severity].filter(Boolean).join(", ");
          if (tail) {
            const nw = doc.getTextWidth(clean(f.name));
            font(8.5, "normal", C.muted);
            write(clean(tail), M + 10 + nw + 8, { baseline: "top" });
          }
          if (Number.isFinite(f.rating)) ratingBar(f.rating, M + CW, 80);
          y += 16;
          if (f.evidence) {
            paragraph(`"${f.evidence}"`, { size: 9.5, style: "italic", color: C.muted, indent: 10, gap: 4 });
          }
          y += 2;
        });
      }
      y += 16;
    });
  }

  /* ---------- Transcript ---------- */

  const t = report.transcript;
  const hasTranscript = t && ((t.type === "timed" && t.lines?.length) || (t.type === "blocks" && t.blocks?.length));
  if (hasTranscript) {
    doc.addPage();
    y = TOP;
    section("Full transcript");

    if (t.type === "timed") {
      const col = 46;
      t.lines.forEach((line) => {
        font(10, "normal", C.ink2);
        const lines = doc.splitTextToSize(clean(line.text), CW - col);
        ensure(lh(10) * Math.min(lines.length, 2));
        font(9, "bold", C.brand);
        write(clean(line.time), M, { baseline: "top" });
        lines.forEach((l) => {
          ensure(lh(10));
          font(10, "normal", C.ink2);
          write(l, M + col);
          y += lh(10);
        });
        y += 4;
      });
    } else {
      t.blocks.forEach((b) => {
        if (b.label) {
          ensure(40);
          font(9.5, "bold", C.brand);
          write(clean(b.label), M);
          y += 16;
        }
        b.paragraphs.forEach((p) => paragraph(p, { size: 10, gap: 8 }));
        y += 8;
      });
    }
  }

  /* ---------- Footer on every page ---------- */

  const pages = doc.getNumberOfPages();
  const shortName = clean(report.filename).length > 70 ? `${clean(report.filename).slice(0, 67)}...` : clean(report.filename);
  for (let i = 1; i <= pages; i += 1) {
    doc.setPage(i);
    doc.setDrawColor(...C.line);
    doc.setLineWidth(0.6);
    doc.line(M, H - 42, M + CW, H - 42);
    font(8, "normal", C.muted);
    doc.text(shortName, M, H - 32, { baseline: "top" });
    doc.text(`Page ${i} of ${pages}`, M + CW, H - 32, { baseline: "top", align: "right" });
  }

  doc.save(fileName);
}