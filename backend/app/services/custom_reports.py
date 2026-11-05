"""
Custom reports from a prompt (Excel / PowerPoint), for one product or many.

  prompt --(AI, or the keyword reader when the AI is unavailable)--> plan
  plan   --(validate against what exists and what this person may see)--> checked plan
  plan   --(real surveys, filtered; statistics on exactly those surveys)--> dataset
  dataset --> .xlsx / .pptx

The AI only decides WHAT goes into the file (products, rounds, filters, sections,
slides). Every number comes from the database, so nothing can be invented.

Plan (JSON):
  title           text
  product_ids     [ids]
  rounds          "latest" | "all" | [round numbers]   (per product)
  compare_rounds  true: one block per round, compared side by side
  filters         {cities, countries, respondent_types, audience, occupations, sec_classes, genders}
  sections        subset of SECTIONS (empty = the standard set)
  breakdowns      subset of DIMENSIONS
  focus           "positive" | "negative" | null
  max_slides      PowerPoint: at most this many slides
"""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import Category, ConsolidatedReport, Product, Respondent, SurveyRound

SECTIONS = {
    "summary": "Summary",
    "kpis": "Key results",
    "ratings": "Rating distribution",
    "likes": "What customers like",
    "concerns": "Concerns",
    "recommendations": "Recommended actions",
    "recipe_recommendations": "Recipe recommendations",
    "questions": "Question results",
    "breakdowns": "Breakdowns",
    "comparison": "Comparison",
    "surveys": "Survey list",
}
STANDARD = ["summary", "kpis", "ratings", "likes", "concerns", "recommendations", "recipe_recommendations",
            "questions", "breakdowns", "comparison", "surveys"]
DIMENSIONS = {
    "respondent_type": "Respondent type", "audience": "Internal / External", "city": "City", "country": "Country",
    "occupation": "Occupation", "sec_class": "Class (SEC)", "gender": "Gender", "age_group": "Age group",
}
FILTER_KEYS = ("cities", "countries", "respondent_types", "occupations", "sec_classes", "genders")


class PlanError(ValueError):
    pass


# ---------------------------------------------------------------- options

def visible_products(db: Session, user) -> list:
    """Products this person may report on (admins: started rounds; Super Admin: all)."""
    q = db.query(Product).filter(Product.is_active == 1)
    if getattr(user, "role", None) == "admin":
        from app.services.product_access import visible_product_ids

        ids = visible_product_ids(db, user)
        if ids is not None:
            q = q.filter(Product.id.in_(ids or {-1}))
    return q.order_by(Product.name).all()


def options(db: Session, user) -> dict:
    """What can be asked for: products with their rounds, and the filter values that exist."""
    products = visible_products(db, user)
    cats = {c.id: c.name for c in db.query(Category).all()}
    rounds = {}
    for r in db.query(SurveyRound).filter(SurveyRound.product_id.in_([p.id for p in products] or [-1])).order_by(SurveyRound.number):
        rounds.setdefault(r.product_id, []).append(r.number)
    people = db.query(Respondent).all()

    def values(attr):
        return sorted({(getattr(p, attr) or "").strip() for p in people if (getattr(p, attr) or "").strip()}, key=str.lower)

    return {
        "products": [{"id": p.id, "name": p.name, "category": cats.get(p.category_id), "rounds": rounds.get(p.id, [])} for p in products],
        "cities": values("location"), "countries": values("country"), "respondent_types": values("respondent_type"),
        "occupations": values("occupation"), "sec_classes": values("sec_class"), "genders": values("gender"),
        "sections": SECTIONS, "dimensions": DIMENSIONS,
    }


# ---------------------------------------------------------------- planning

def _llm_plan(prompt: str, fmt: str, opts: dict, context_ids: list) -> Optional[dict]:
    from app.services.ai_client import llm_generate

    catalogue = [{"id": p["id"], "name": p["name"], "category": p["category"], "rounds": p["rounds"]} for p in opts["products"]]
    ask = f"""You turn a request for a {'PowerPoint' if fmt == 'pptx' else 'Excel'} consumer-feedback report into a JSON plan.
Products (only use these ids): {json.dumps(catalogue, ensure_ascii=False)}
Products of the page the request comes from: {context_ids or "none (pick from the request)"}
Known filter values: cities={opts['cities'][:80]} countries={opts['countries'][:40]} respondent_types={opts['respondent_types']}
occupations={opts['occupations']} sec_classes={opts['sec_classes']} genders={opts['genders']}
Sections: {list(SECTIONS)}  Breakdown dimensions: {list(DIMENSIONS)}
Request: {prompt}

Answer ONLY with JSON: {{"title": "...", "product_ids": [..], "rounds": "latest" | "all" | [numbers], "compare_rounds": false,
"filters": {{"cities": [], "countries": [], "respondent_types": [], "audience": null, "occupations": [], "sec_classes": [], "genders": []}},
"sections": [..], "breakdowns": [..], "focus": null, "max_slides": null}}
Rules: never invent products or filter values; use the page's products unless the request names others or says all;
"compare rounds" -> compare_rounds true and rounds "all"; audience is "internal", "external" or null; focus is "positive", "negative" or null."""
    raw = llm_generate(ask, num_predict=700, json_mode=True, timeout=90)
    m = re.search(r"\{.*\}", raw or "", re.S)
    return json.loads(m.group()) if m else None


def _keyword_plan(prompt: str, opts: dict, context_ids: list) -> dict:
    """The same plan from keywords, when the AI service is unavailable."""
    text = " " + (prompt or "").lower() + " "
    from app.services.product_check import aliases

    named = [p["id"] for p in opts["products"] if any(f" {a} " in re.sub(r"[^a-z0-9' ]", " ", text) for a in aliases(p["name"]))]
    cats = [p["id"] for p in opts["products"] if p["category"] and p["category"].lower() in text]
    every = bool(re.search(r"\b(all|every|each)\s+(the\s+)?products?\b|\bacross products\b", text))
    ids = [p["id"] for p in opts["products"]] if every else (named or cats or context_ids)
    rounds_found = [int(n) for n in re.findall(r"round\s*(\d+)", text)]
    compare = bool(re.search(r"\bcompar|\bvs\b|\bversus\b", text)) and not named[1:] and not every
    plan = {
        "title": "", "product_ids": ids,
        "rounds": rounds_found or ("all" if compare or "all rounds" in text else "latest"),
        "compare_rounds": compare or len(rounds_found) > 1,
        # "retailers" matches "Retailer": plural forms count
        "filters": {k: [v for v in opts[k] if re.search(r"\b" + re.escape(v.lower()) + r"(s|es)?\b", text)] for k in FILTER_KEYS},
        "sections": [], "breakdowns": [], "focus": None, "max_slides": None,
    }
    plan["filters"]["audience"] = "internal" if " internal " in text else "external" if " external " in text else None
    slides = re.search(r"(\d+)\s*(slides|pages)", text)
    if slides:
        plan["max_slides"] = int(slides.group(1))
    if re.search(r"complain|concern|issue|problem|negative|weak|dislike", text):
        plan["focus"] = "negative"
        plan["sections"] += ["kpis", "concerns", "recommendations", "recipe_recommendations"]
    if re.search(r"\blike|positive|praise|strength|good", text):
        plan["focus"] = None if plan["focus"] else "positive"
        plan["sections"] += ["kpis", "likes"]
    if re.search(r"summary|executive|overview|brief", text):
        plan["sections"] = ["summary", "kpis", "likes", "concerns", "recommendations"] + plan["sections"]
    for key, label in DIMENSIONS.items():
        if label.lower().split(" (")[0] in text or key.replace("_", " ") in text:
            plan["breakdowns"].append(key)
    if plan["breakdowns"] and "breakdowns" not in plan["sections"] and plan["sections"]:
        plan["sections"].append("breakdowns")
    if re.search(r"question|answers", text) and plan["sections"]:
        plan["sections"].append("questions")
    if re.search(r"list of surveys|each survey|every survey|raw data|all responses", text):
        plan["sections"].append("surveys")
    return plan


def make_plan(db: Session, user, prompt: str, fmt: str, context_ids: list, context_round: Optional[int] = None) -> tuple:
    """(checked plan, warnings, planned_by)."""
    opts = options(db, user)
    prompt = (prompt or "").strip()
    planned_by = "standard"
    raw = {"product_ids": context_ids, "rounds": [context_round] if context_round else "latest"}
    if prompt:
        try:
            raw = _llm_plan(prompt, fmt, opts, context_ids) or {}
            planned_by = "ai"
        except Exception as e:  # AI service down or slow: the keyword reader
            print(f"Custom report: AI planning unavailable ({e}); using keywords.", flush=True)
            raw = _keyword_plan(prompt, opts, context_ids)
            planned_by = "keywords"
    plan, warnings = check_plan(raw, opts, fmt, context_ids, prompt)
    return plan, warnings, planned_by


def check_plan(raw: dict, opts: dict, fmt: str, context_ids: list, prompt: str = "") -> tuple:
    """Keep only what exists and what this person may see; say what was dropped."""
    warnings = []
    known = {p["id"]: p for p in opts["products"]}
    ids = []
    for i in raw.get("product_ids") or []:
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if i in known and i not in ids:
            ids.append(i)
    if not ids:
        ids = [i for i in context_ids if i in known]
    if not ids:
        raise PlanError("Choose at least one product (or name one in the request).")
    rounds = raw.get("rounds") or "latest"
    if isinstance(rounds, list):
        rounds = sorted({int(r) for r in rounds if str(r).isdigit()}) or "latest"
    elif rounds not in ("latest", "all"):
        rounds = "latest"
    filters = {}
    for key in FILTER_KEYS:
        lower = {v.lower(): v for v in opts[key]}
        picked = []
        for v in (raw.get("filters") or {}).get(key) or []:
            v = str(v).strip()
            if v.lower() in lower:
                picked.append(lower[v.lower()])
            elif v:
                warnings.append(f"No surveys have {key.replace('_', ' ').rstrip('s')} “{v}”, so it was left out.")
        filters[key] = picked
    audience = (raw.get("filters") or {}).get("audience")
    filters["audience"] = audience if audience in ("internal", "external") else None
    sections = [s for s in (raw.get("sections") or []) if s in SECTIONS]
    sections = list(dict.fromkeys(sections)) or list(STANDARD)
    if (len(ids) > 1 or raw.get("compare_rounds")) and "comparison" not in sections:
        sections.insert(1, "comparison")
    breakdowns = [d for d in (raw.get("breakdowns") or []) if d in DIMENSIONS] or ["respondent_type", "audience", "city", "country"]
    max_slides = raw.get("max_slides")
    try:
        max_slides = max(2, min(40, int(max_slides))) if max_slides else None
    except (TypeError, ValueError):
        max_slides = None
    names = ", ".join(known[i]["name"] for i in ids[:3]) + (f" and {len(ids) - 3} more" if len(ids) > 3 else "")
    plan = {
        "format": fmt, "prompt": prompt,
        "title": (str(raw.get("title") or "").strip()[:120] or f"{names} · consumer report"),
        "product_ids": ids, "rounds": rounds, "compare_rounds": bool(raw.get("compare_rounds")),
        "filters": filters, "sections": sections, "breakdowns": breakdowns,
        "focus": raw.get("focus") if raw.get("focus") in ("positive", "negative") else None,
        "max_slides": max_slides if fmt == "pptx" else None,
    }
    return plan, warnings


def describe(plan: dict, opts_products: dict) -> str:
    """One line: what the file will contain."""
    parts = ["PowerPoint" if plan["format"] == "pptx" else "Excel"]
    if plan.get("max_slides"):
        parts.append(f"up to {plan['max_slides']} slides")
    names = [opts_products.get(i, f"#{i}") for i in plan["product_ids"]]
    parts.append(", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else ""))
    r = plan["rounds"]
    parts.append("latest round" if r == "latest" else "all rounds" if r == "all" else "round " + ", ".join(map(str, r)))
    if plan["compare_rounds"]:
        parts.append("rounds compared")
    f = plan["filters"]
    for key in FILTER_KEYS:
        if f.get(key):
            parts.append(" / ".join(f[key]))
    if f.get("audience"):
        parts.append(f["audience"].title())
    if plan.get("focus"):
        parts.append("focus: " + ("what customers like" if plan["focus"] == "positive" else "concerns"))
    parts.append("sections: " + ", ".join(SECTIONS[s] for s in plan["sections"]))
    return " · ".join(parts)


# ---------------------------------------------------------------- data

def _match(plan: dict, resp, person) -> bool:
    f = plan["filters"]

    def has(values, value):
        return not values or (value or "").strip().lower() in {v.lower() for v in values}

    return (has(f["cities"], getattr(person, "location", None)) and has(f["countries"], getattr(person, "country", None))
            and has(f["respondent_types"], getattr(person, "respondent_type", None))
            and has(f["occupations"], getattr(person, "occupation", None))
            and has(f["sec_classes"], getattr(person, "sec_class", None)) and has(f["genders"], getattr(person, "gender", None))
            and (not f.get("audience") or resp.audience == f["audience"]))


def _filtered(plan: dict) -> bool:
    f = plan["filters"]
    return bool(f.get("audience")) or any(f.get(k) for k in FILTER_KEYS)


def build_dataset(db: Session, plan: dict) -> list:
    """Blocks of real data: one per product (and per round when rounds are compared)."""
    from app.api.product_feedback import _analyzed_pairs, compose_items
    from app.services.consolidation import build_stats

    blocks = []
    for pid in plan["product_ids"]:
        product = db.get(Product, pid)
        if product is None:
            continue
        all_rounds = db.query(SurveyRound).filter(SurveyRound.product_id == pid).order_by(SurveyRound.number).all()
        pairs = _analyzed_pairs(db, [pid])
        people = {p.id: p for p in db.query(Respondent).filter(Respondent.id.in_({r.respondent_id for r, _ in pairs} or {-1})).all()}
        pairs = [(r, rec) for r, rec in pairs if _match(plan, r, people.get(r.respondent_id))]
        with_data = [r for r in all_rounds if any(resp.round_id == r.id for resp, _ in pairs)]
        if plan["rounds"] == "latest":
            chosen = with_data[-1:] or all_rounds[-1:]
        elif plan["rounds"] == "all":
            chosen = all_rounds
        else:
            chosen = [r for r in all_rounds if r.number in plan["rounds"]]
        groups = [[r] for r in chosen] if plan["compare_rounds"] or plan["rounds"] == "latest" else [chosen]
        for group in groups:
            ids = {r.id for r in group}
            subset = [(r, rec) for r, rec in pairs if (r.round_id in ids if group else True)]
            response_ids, items, _, _, _ = compose_items(db, product, subset, translate=False) if subset else ([], [], [], {}, 0)
            stats = build_stats(items) if items else None
            narrative = None
            if len(group) == 1 and not _filtered(plan):
                rep = (db.query(ConsolidatedReport).filter(ConsolidatedReport.round_id == group[0].id)
                       .order_by(ConsolidatedReport.id.desc()).first())
                narrative = rep.narrative if rep else None
            label = product.name + (f" · Round {group[0].number}" if len(group) == 1 else
                                    f" · Rounds {', '.join(str(r.number) for r in group)}" if group else "")
            blocks.append({"product": product, "rounds": group, "label": label, "items": items, "stats": stats,
                           "narrative": narrative, "pairs": subset, "people": people})
    return blocks


def _likes(block, n=6):
    aspects = (block["stats"] or {}).get("aspects") or []
    rows = sorted((a for a in aspects if a.get("positive")), key=lambda a: -a["positive"])[:n]
    return [(a["name"], a["positive"], (a.get("evidence") or [""])[0]) for a in rows]


def _concerns(block, n=6):
    aspects = (block["stats"] or {}).get("aspects") or []
    rows = sorted((a for a in aspects if a.get("negative")), key=lambda a: -a["negative"])[:n]
    return [(a["name"], a["negative"], (a.get("evidence") or [""])[0]) for a in rows]


def _actions(block):
    nar = block["narrative"] or {}
    acts = [x if isinstance(x, str) else str(x.get("point") or x.get("action") or "") for x in nar.get("recommended_actions") or []]
    if acts:
        return [a for a in acts if a]
    n = (block["stats"] or {}).get("response_count") or 0
    return [f"Look into {name.lower()}: raised by {count} of {n} customers." for name, count, _ in _concerns(block, 4)]


def _kpi(block):
    s = block["stats"] or {}
    sent = s.get("sentiment_distribution") or {}
    n = s.get("response_count") or 0
    return n, s.get("overall_rating"), sent.get("positive", 0), sent.get("mixed", 0), sent.get("negative", 0)


# ---------------------------------------------------------------- Excel

def build_xlsx(db: Session, plan: dict, blocks: list) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head, fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="0A4174")

    def table(ws, headers, rows, wide=()):
        ws.append(headers)
        for c in ws[ws.max_row]:
            c.font, c.fill = head, fill
        for r in rows:
            ws.append(list(r))
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(i)].width = 60 if h in wide else max(12, min(36, len(str(h)) + 6))
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Summary"
    opts = {p.id: p.name for p in db.query(Product).filter(Product.id.in_(plan["product_ids"])).all()}
    for label, value in [("Report", plan["title"]), ("Request", plan["prompt"] or "Standard report"),
                         ("Contents", describe(plan, opts)), ("Created", datetime.utcnow().strftime("%d %b %Y %H:%M UTC"))]:
        ws.append([label, value])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 16, 110
    for row in ws.iter_rows(min_col=2, max_col=2):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")
    sec = set(plan["sections"])
    if sec & {"kpis", "comparison", "summary"}:
        rows = []
        for b in blocks:
            n, rating, pos, mix, neg = _kpi(b)
            top_like = (_likes(b, 1) or [("", "", "")])[0][0]
            top_concern = (_concerns(b, 1) or [("", "", "")])[0][0]
            rows.append([b["label"], n, rating, pos, mix, neg, top_like, top_concern])
        table(wb.create_sheet("Results"), ["Product / round", "Surveys", "Average rating", "Positive", "Mixed", "Negative", "Top like", "Top concern"], rows)
    if sec & {"likes", "concerns", "recommendations", "recipe_recommendations"}:
        rows = []
        for b in blocks:
            n = _kpi(b)[0]
            if "likes" in sec and plan.get("focus") != "negative":
                rows += [[b["label"], "Customers like", name, count, n, ev] for name, count, ev in _likes(b)]
            if "concerns" in sec and plan.get("focus") != "positive":
                rows += [[b["label"], "Concern", name, count, n, ev] for name, count, ev in _concerns(b)]
            if "recommendations" in sec:
                rows += [[b["label"], "Recommended action", a, None, None, ""] for a in _actions(b)]
            if "recipe_recommendations" in sec:
                rows += [[b["label"], "Recipe recommendation", f"{x.get('change')} (why: {x.get('reason')})", x.get("customers"), n, ""]
                         for x in (b["narrative"] or {}).get("recipe_recommendations") or []]
        table(wb.create_sheet("Findings"), ["Product / round", "Kind", "Finding", "Customers", "Of surveys", "Customer's words"], rows, wide=("Finding", "Customer's words"))
    if "ratings" in sec:
        rows = []
        for b in blocks:
            for star, count in sorted(((b["stats"] or {}).get("rating_distribution") or {}).items()):
                rows.append([b["label"], f"{star}★", count])
        table(wb.create_sheet("Ratings"), ["Product / round", "Rating", "Surveys"], rows)
    if "questions" in sec:
        rows = []
        for b in blocks:
            for q in (b["stats"] or {}).get("questions") or []:
                if q.get("distribution"):
                    rows += [[b["label"], q.get("question"), opt, cnt, q.get("answered")] for opt, cnt in q["distribution"].items()]
                elif q.get("average") is not None:
                    rows.append([b["label"], q.get("question"), "Average", q.get("average"), q.get("answered")])
        table(wb.create_sheet("Questions"), ["Product / round", "Question", "Answer", "Count", "Answered"], rows, wide=("Question",))
    if "breakdowns" in sec:
        rows = []
        for b in blocks:
            for dim in plan["breakdowns"]:
                for g in ((b["stats"] or {}).get("breakdowns") or {}).get(dim) or []:
                    rows.append([b["label"], DIMENSIONS[dim], g.get("group"), g.get("responses"), g.get("avg_rating"), g.get("positive"), g.get("mixed"), g.get("negative")])
        table(wb.create_sheet("Breakdowns"), ["Product / round", "Dimension", "Group", "Surveys", "Average rating", "Positive", "Mixed", "Negative"], rows)
    if "surveys" in sec:
        rows = []
        for b in blocks:
            for resp, rec in b["pairs"]:
                if b["rounds"] and resp.round_id not in {r.id for r in b["rounds"]}:
                    continue
                p = b["people"].get(resp.respondent_id)
                rows.append([b["label"], resp.id, resp.created_at.strftime("%d %b %Y") if resp.created_at else "", resp.executive_name or "",
                             getattr(p, "location", "") or "", getattr(p, "country", "") or "", getattr(p, "respondent_type", "") or "",
                             (resp.audience or "").title(), rec.overall_rating, rec.overall_sentiment or "", (rec.overall_summary or "")[:800]])
        table(wb.create_sheet("Surveys"), ["Product / round", "Survey #", "Date", "Survey person", "City", "Country", "Respondent type",
                                           "Internal / External", "Rating", "Sentiment", "Summary"], rows, wide=("Summary",))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- PowerPoint

def build_pptx(db: Session, plan: dict, blocks: list) -> bytes:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches, Pt

    navy, muted, ink = RGBColor(0x0A, 0x41, 0x74), RGBColor(0x56, 0x6A, 0x7E), RGBColor(0x00, 0x1D, 0x39)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    slides = []  # (priority, builder): lower priority number = kept first when max_slides cuts

    def text(slide, x, y, w, h, value, size=18, bold=False, color=None):
        tf = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text, p.font.size, p.font.bold = str(value), Pt(size), bold
        p.font.color.rgb = color or ink
        return tf

    def titled(title, sub=""):
        s = prs.slides.add_slide(blank)
        text(s, 0.6, 0.4, 12.1, 0.8, title, 30, True, navy)
        if sub:
            text(s, 0.6, 1.1, 12.1, 0.5, sub, 14, False, muted)
        return s

    def bullets(s, lines, y=1.8, size=18):
        tf = text(s, 0.6, y, 12.1, 5.2, "", size)
        for i, line in enumerate(lines[:9] or ["Nothing to show for this selection."]):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text, p.font.size = "• " + line, Pt(size)
            p.space_after = Pt(8)

    def grid(s, headers, rows, y=1.8):
        rows = rows[:12]
        t = s.shapes.add_table(len(rows) + 1, len(headers), Inches(0.6), Inches(y), Inches(12.1), Inches(0.4 * (len(rows) + 1))).table
        for c, h in enumerate(headers):
            t.cell(0, c).text = str(h)
        for r_i, row in enumerate(rows, 1):
            for c, v in enumerate(row):
                t.cell(r_i, c).text = "–" if v is None else str(v)

    opts = {p.id: p.name for p in db.query(Product).filter(Product.id.in_(plan["product_ids"])).all()}
    sec = set(plan["sections"])
    many = len(blocks) > 1

    def cover():
        s = prs.slides.add_slide(blank)
        text(s, 0.8, 2.3, 11.7, 1.4, plan["title"], 40, True, navy)
        text(s, 0.8, 3.9, 11.7, 1.2, describe(plan, opts), 16, False, muted)
        text(s, 0.8, 5.4, 11.7, 0.5, datetime.utcnow().strftime("%d %b %Y"), 14, False, muted)
    slides.append((0, cover))

    if many and ("comparison" in sec or "kpis" in sec):
        def compare():
            s = titled("Comparison", f"{len(blocks)} products / rounds · numbers from the surveys")
            rows = []
            for b in blocks:
                n, rating, pos, mix, neg = _kpi(b)
                rows.append([b["label"], n, rating, pos, mix, neg])
            grid(s, ["Product / round", "Surveys", "Avg rating", "Positive", "Mixed", "Negative"], rows)
            rated = [b for b in blocks if _kpi(b)[1] is not None][:12]
            if rated and len(rows) <= 6:
                data = CategoryChartData()
                data.categories = [b["label"][:28] for b in rated]
                data.add_series("Average rating", [_kpi(b)[1] for b in rated])
                ch = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.6), Inches(1.9 + 0.4 * (len(rows) + 1)), Inches(12.1),
                                        Inches(max(2.0, 5.2 - 0.4 * (len(rows) + 1))), data).chart
                ch.has_legend = False
        slides.append((1, compare))

    for b in blocks:
        n, rating, pos, mix, neg = _kpi(b)
        sub = f"{b['label']} · {n} surveys"
        if "kpis" in sec or "summary" in sec:
            def kpis(b=b, sub=sub, n=n, rating=rating, pos=pos, mix=mix, neg=neg):
                s = titled(f"Key results · {b['product'].name}", sub)
                for i, (label, value) in enumerate([("Surveys", n), ("Average rating", f"{rating if rating is not None else '–'} / 5"),
                                                     ("Positive · Mixed · Negative", f"{pos} · {mix} · {neg}")]):
                    text(s, 0.6 + i * 4.2, 1.9, 4, 0.4, label, 14, False, muted)
                    text(s, 0.6 + i * 4.2, 2.3, 4, 0.8, value, 30, True, navy)
                dist = (b["stats"] or {}).get("rating_distribution") or {}
                if dist and "ratings" in sec:
                    data = CategoryChartData()
                    data.categories = [f"{k}★" for k in sorted(dist)]
                    data.add_series("Surveys", [dist[k] for k in sorted(dist)])
                    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(3.4), Inches(12.1), Inches(3.6), data).chart.has_legend = False
                conclusion = (b["narrative"] or {}).get("conclusion")
                if conclusion:
                    text(s, 0.6, 7.0, 12.1, 0.4, conclusion[:220], 12, False, muted)
            slides.append((2, kpis))
        if "likes" in sec and plan.get("focus") != "negative":
            slides.append((4 if plan.get("focus") != "positive" else 3, lambda b=b, sub=sub, n=n: bullets(
                titled(f"What customers like · {b['product'].name}", sub), [f"{name}: {c} of {n} customers" + (f" · “{ev[:90]}”" if ev else "") for name, c, ev in _likes(b)])))
        if "concerns" in sec and plan.get("focus") != "positive":
            slides.append((3 if plan.get("focus") == "negative" else 4, lambda b=b, sub=sub, n=n: bullets(
                titled(f"Concerns · {b['product'].name}", sub), [f"{name}: {c} of {n} customers" + (f" · “{ev[:90]}”" if ev else "") for name, c, ev in _concerns(b)])))
        if "recommendations" in sec:
            slides.append((5, lambda b=b, sub=sub: bullets(titled(f"Recommended actions · {b['product'].name}", sub), _actions(b))))
        recs = (b["narrative"] or {}).get("recipe_recommendations") or []
        if "recipe_recommendations" in sec and recs:
            slides.append((6, lambda b=b, sub=sub, recs=recs: bullets(titled(f"Recipe recommendations · {b['product'].name}", sub + " · suggestions only"),
                                                                      [f"{x.get('change')}: {x.get('reason')} ({x.get('customers')} customers)" for x in recs])))
        if "breakdowns" in sec:
            for dim in plan["breakdowns"]:
                groups = ((b["stats"] or {}).get("breakdowns") or {}).get(dim) or []
                if not groups or all(g.get("group") == "Not given" for g in groups):
                    continue
                slides.append((7, lambda b=b, sub=sub, dim=dim, groups=groups: grid(
                    titled(f"By {DIMENSIONS[dim].lower()} · {b['product'].name}", sub), [DIMENSIONS[dim], "Surveys", "Avg rating", "Positive", "Mixed", "Negative"],
                    [[g.get("group"), g.get("responses"), g.get("avg_rating"), g.get("positive"), g.get("mixed"), g.get("negative")] for g in groups])))
        if "questions" in sec:
            for q in [q for q in (b["stats"] or {}).get("questions") or [] if q.get("distribution")][:4]:
                slides.append((8, lambda b=b, sub=sub, q=q: grid(titled(q.get("question")[:90], sub), ["Answer", "Customers"], list(q["distribution"].items()))))

    limit = plan.get("max_slides")
    order = sorted(range(len(slides)), key=lambda i: (slides[i][0], i))
    keep = set(order[:limit]) if limit else set(order)
    for i, (_, build) in enumerate(slides):
        if i in keep:
            build()
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def filename(plan: dict) -> str:
    base = re.sub(r'[\\/:*?"<>|]+', "-", plan["title"])[:80].strip() or "Consumer report"
    return f"{base}.{plan['format']}"


def counts(blocks: list) -> Counter:
    return Counter({b["label"]: (b["stats"] or {}).get("response_count", 0) for b in blocks})


def content_disposition(name: str) -> str:
    """Download header: a plain-ASCII name (HTTP headers allow no other) plus the full UTF-8 name."""
    from urllib.parse import quote

    ascii_name = name.encode("ascii", "ignore").decode().replace('"', "") or "report"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"

