"""
Report exports (requirements points 6 and 7), from the same consolidated report the
screens show, so the numbers always match.

GET /product-feedback/{product_id}/export.xlsx?round=N   Excel: summary, every survey (with its
                                                         answers and dimensions), question results,
                                                         breakdowns by respondent type, Internal /
                                                         External, city, country, occupation, class
GET /product-feedback/{product_id}/export.pptx?round=N   PowerPoint: KPIs, rating chart, likes,
                                                         concerns, recommendations, recipe
                                                         recommendations, breakdowns
Without round: the latest round with a report. Admins and the Super Admin.
"""

import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.ownership import account
from app.services.custom_reports import content_disposition
from app.database.database import get_db
from app.database.models import (Category, ConsolidatedReport, FeedbackRecord, Product, Respondent, SurveyAnswer,
                                 SurveyResponse, SurveyRound)

router = APIRouter(tags=["Report exports"])

BREAKDOWNS = [("respondent_type", "Respondent type"), ("audience", "Internal / External"), ("city", "City"),
              ("country", "Country"), ("occupation", "Occupation"), ("sec_class", "Class (SEC)"),
              ("gender", "Gender"), ("age_group", "Age group")]
SAFE = str.maketrans({c: "-" for c in '\\/:*?"<>|'})


def _report(db: Session, request: Request, product_id: int, round_number: Optional[int]):
    if account(request).get("role") not in ("admin", "client"):
        raise HTTPException(status_code=403, detail="Only admins and the Super Admin can download reports.")
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    rounds = db.query(SurveyRound).filter(SurveyRound.product_id == product_id).order_by(SurveyRound.number.desc()).all()
    if round_number is not None:
        rounds = [r for r in rounds if r.number == round_number]
        if not rounds:
            raise HTTPException(status_code=404, detail=f"Round {round_number} not found.")
    for rnd in rounds:
        rep = (db.query(ConsolidatedReport).filter(ConsolidatedReport.round_id == rnd.id)
               .order_by(ConsolidatedReport.id.desc()).first())
        if rep is not None:
            return product, rnd, rep
    raise HTTPException(status_code=404, detail="No report yet: analyze some surveys first.")


def _surveys(db: Session, rep: ConsolidatedReport) -> list:
    ids = [int(i) for i in (rep.response_ids or [])]
    if not ids:
        return []
    responses = db.query(SurveyResponse).filter(SurveyResponse.id.in_(ids)).order_by(SurveyResponse.created_at).all()
    people = {p.id: p for p in db.query(Respondent).filter(Respondent.id.in_({r.respondent_id for r in responses})).all()}
    records = {}
    for rec in db.query(FeedbackRecord).filter(FeedbackRecord.response_id.in_(ids)).order_by(FeedbackRecord.id):
        records[rec.response_id] = rec
    answers = {}
    for a in db.query(SurveyAnswer).filter(SurveyAnswer.response_id.in_(ids)).order_by(SurveyAnswer.id):
        answers.setdefault(a.response_id, []).append(a)
    out = []
    for r in responses:
        p, rec = people.get(r.respondent_id), records.get(r.id)
        out.append({"r": r, "p": p, "rec": rec, "answers": answers.get(r.id, [])})
    return out


def _filename(product: Product, rnd: SurveyRound, ext: str) -> str:
    return f"{product.name} - Round {rnd.number} report.{ext}".translate(SAFE)


# ---------------- Excel ----------------

@router.get("/product-feedback/{product_id}/export.xlsx")
def export_xlsx(product_id: int, request: Request, round: Optional[int] = None, db: Session = Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    product, rnd, rep = _report(db, request, product_id, round)
    stats, narrative = rep.stats or {}, rep.narrative or {}
    category = db.get(Category, product.category_id)
    surveys = _surveys(db, rep)
    wb = Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="0A4174")

    def table(ws, headers, rows, widths=None):
        ws.append(headers)
        for cell in ws[ws.max_row]:
            cell.font, cell.fill = head, fill
        for row in rows:
            ws.append(row)
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, max(12, min(48, len(str(h)) + 6)))
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Summary"
    sent = stats.get("sentiment_distribution") or {}
    for label, value in [("Product", product.name), ("Category", category.name if category else ""), ("SKU", product.sku or ""),
                         ("Round", rnd.number), ("Recipe version", rnd.recipe_version), ("Surveys in the report", rep.response_count),
                         ("Average rating (out of 5)", stats.get("overall_rating")), ("Positive", sent.get("positive", 0)),
                         ("Mixed", sent.get("mixed", 0)), ("Negative", sent.get("negative", 0)),
                         ("Conclusion", narrative.get("conclusion") or ""),
                         ("Report generated", rep.generated_at.strftime("%d %b %Y %H:%M") if rep.generated_at else ""),
                         ("Downloaded", datetime.utcnow().strftime("%d %b %Y %H:%M UTC"))]:
        ws.append([label, value])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 28, 90
    for row in ws.iter_rows(min_col=2, max_col=2):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")

    questions = []
    for s in surveys:
        for a in s["answers"]:
            if a.question_text not in questions:
                questions.append(a.question_text)
    ws = wb.create_sheet("Surveys")
    base = ["Survey #", "Date", "Survey person", "City", "Country", "Respondent type", "Internal / External",
            "Occupation", "Class (SEC)", "Gender", "Age", "Rating", "Sentiment", "Summary"]
    rows = []
    for s in surveys:
        r, p, rec = s["r"], s["p"], s["rec"]
        by_q = {a.question_text: a.value for a in s["answers"]}
        rows.append([r.id, r.created_at.strftime("%d %b %Y") if r.created_at else "", r.executive_name or "",
                     p.location if p else "", p.country if p else "", p.respondent_type if p else "",
                     (r.audience or "").title(), p.occupation if p else "", p.sec_class if p else "",
                     p.gender if p else "", p.age if p else "", rec.overall_rating if rec else None,
                     rec.overall_sentiment if rec else "", (rec.overall_summary or "")[:1000] if rec else ""]
                    + [by_q.get(q) or "" for q in questions])
    table(ws, base + questions, rows, {"Summary": 60})

    ws = wb.create_sheet("Questions")
    qrows = []
    for q in stats.get("questions") or []:
        if q.get("distribution"):
            for option, count in q["distribution"].items():
                qrows.append([q.get("question"), q.get("type"), option, count, q.get("answered")])
        elif q.get("average") is not None:
            qrows.append([q.get("question"), q.get("type"), "Average", q.get("average"), q.get("answered")])
        else:
            for t in (q.get("text_answers") or [])[:50]:
                qrows.append([q.get("question"), q.get("type"), t, None, q.get("answered")])
    table(ws, ["Question", "Type", "Answer", "Count", "Answered"], qrows, {"Question": 50, "Answer": 50})

    ws = wb.create_sheet("Breakdowns")
    brows = []
    for key, label in BREAKDOWNS:
        for g in (stats.get("breakdowns") or {}).get(key) or []:
            brows.append([label, g.get("group"), g.get("responses"), g.get("avg_rating"), g.get("positive"), g.get("mixed"), g.get("negative")])
    table(ws, ["Dimension", "Group", "Surveys", "Average rating", "Positive", "Mixed", "Negative"], brows, {"Group": 28})

    ws = wb.create_sheet("Findings")
    frows = [["Customers like", x.get("point") or x.get("theme"), x.get("customers")] for x in narrative.get("common_positives") or []]
    frows += [["Concerns", x.get("point") or x.get("theme"), x.get("customers")] for x in narrative.get("recurring_issues") or []]
    frows += [["Recommended action", x if isinstance(x, str) else (x.get("point") or x.get("action")), None]
              for x in narrative.get("recommended_actions") or []]
    frows += [["Recipe recommendation", f"{x.get('change')} (why: {x.get('reason')})", x.get("customers")]
              for x in narrative.get("recipe_recommendations") or []]
    table(ws, ["Kind", "Finding", "Customers"], frows, {"Finding": 90})

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = _filename(product, rnd, "xlsx")
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": content_disposition(name)})


# ---------------- PowerPoint ----------------

@router.get("/product-feedback/{product_id}/export.pptx")
def export_pptx(product_id: int, request: Request, round: Optional[int] = None, db: Session = Depends(get_db)):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches, Pt

    product, rnd, rep = _report(db, request, product_id, round)
    stats, narrative = rep.stats or {}, rep.narrative or {}
    navy, muted = RGBColor(0x0A, 0x41, 0x74), RGBColor(0x56, 0x6A, 0x7E)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    def text(slide, x, y, w, h, value, size=18, bold=False, color=None):
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = str(value)
        p.font.size, p.font.bold = Pt(size), bold
        p.font.color.rgb = color or RGBColor(0x00, 0x1D, 0x39)
        return tf

    def titled(title, subtitle=None):
        s = prs.slides.add_slide(blank)
        text(s, 0.6, 0.4, 12, 0.8, title, 30, True, navy)
        if subtitle:
            text(s, 0.6, 1.1, 12, 0.5, subtitle, 14, False, muted)
        return s

    def bullets(slide, items, y=1.8):
        tf = text(slide, 0.6, y, 12.1, 5.2, "", 18)
        first = True
        for item in items[:9] or ["Nothing to show yet."]:
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            p.text = "• " + item
            p.font.size = Pt(18)
            p.space_after = Pt(8)

    sub = f"{product.name} · Round {rnd.number} · recipe v{rnd.recipe_version} · {rep.response_count} surveys"
    s = prs.slides.add_slide(blank)
    text(s, 0.8, 2.4, 11.5, 1.2, product.name, 44, True, navy)
    text(s, 0.8, 3.6, 11.5, 0.6, f"Consumer report · Round {rnd.number} (recipe v{rnd.recipe_version})", 22, False, muted)
    text(s, 0.8, 4.3, 11.5, 0.6, f"{rep.response_count} surveys · {datetime.utcnow().strftime('%d %b %Y')}", 16, False, muted)

    s = titled("Key results", sub)
    sent = stats.get("sentiment_distribution") or {}
    for i, (label, value) in enumerate([("Surveys", rep.response_count),
                                        ("Average rating", f"{stats.get('overall_rating') or '–'} / 5"),
                                        ("Positive · Mixed · Negative", f"{sent.get('positive', 0)} · {sent.get('mixed', 0)} · {sent.get('negative', 0)}")]):
        text(s, 0.6 + i * 4.2, 1.9, 4, 0.4, label, 14, False, muted)
        text(s, 0.6 + i * 4.2, 2.3, 4, 0.8, value, 30, True, navy)
    dist = stats.get("rating_distribution") or {}
    if dist:
        data = CategoryChartData()
        data.categories = [f"{k}★" for k in sorted(dist)]
        data.add_series("Surveys", [dist[k] for k in sorted(dist)])
        chart = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(3.4), Inches(12), Inches(3.6), data).chart
        chart.has_legend = False
    if narrative.get("conclusion"):
        text(s, 0.6, 7.0, 12, 0.4, narrative["conclusion"][:220], 12, False, muted)

    bullets(titled("What customers like", sub),
            [f"{x.get('point') or x.get('theme')} ({x.get('customers')} customers)" for x in narrative.get("common_positives") or []])
    bullets(titled("Concerns", sub),
            [f"{x.get('point') or x.get('theme')} ({x.get('customers')} customers)" for x in narrative.get("recurring_issues") or []])
    bullets(titled("Recommended actions", sub),
            [x if isinstance(x, str) else str(x.get("point") or x.get("action") or "") for x in narrative.get("recommended_actions") or []])
    recs = narrative.get("recipe_recommendations") or []
    if recs:
        bullets(titled("Recipe recommendations", sub + " · suggestions only, the recipe is not changed"),
                [f"{x.get('change')}: {x.get('reason')} ({x.get('customers')} customers)" for x in recs])

    for key, label in BREAKDOWNS[:6]:
        groups = (stats.get("breakdowns") or {}).get(key) or []
        if not groups or all(g.get("group") == "Not given" for g in groups):
            continue
        s = titled(f"By {label.lower()}", sub)
        rows = groups[:10]
        shape = s.shapes.add_table(len(rows) + 1, 6, Inches(0.6), Inches(1.8), Inches(12.1), Inches(0.4 * (len(rows) + 1)))
        t = shape.table
        for c, h in enumerate([label, "Surveys", "Avg rating", "Positive", "Mixed", "Negative"]):
            t.cell(0, c).text = h
        for r_i, g in enumerate(rows, 1):
            for c, v in enumerate([g.get("group"), g.get("responses"), g.get("avg_rating") if g.get("avg_rating") is not None else "–",
                                   g.get("positive"), g.get("mixed"), g.get("negative")]):
                t.cell(r_i, c).text = str(v)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    name = _filename(product, rnd, "pptx")
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                             headers={"Content-Disposition": content_disposition(name)})
