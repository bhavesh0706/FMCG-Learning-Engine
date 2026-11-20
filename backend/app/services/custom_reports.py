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
    "occupation": "Occupation", "sec_class": "Class (SEC)", "gender": "Gender", "age_group": "Age group", "state": "State",
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

# The prompt box starts with this design brief (web and app); it is also always given to the
# AI planner, and it is removed before the request is read for its content.
DESIGN_BRIEF = "Act as a senior UI/UX designer and create a clear, visually engaging PowerPoint presentation:"
_BRIEF_RE = re.compile(r"^\s*act as a senior ui\s*/\s*ux (designer|developer)[^:]*:\s*", re.I)


def request_text(prompt: str) -> str:
    """The person's own request, without the design brief at its start."""
    return _BRIEF_RE.sub("", prompt or "").strip()


def _llm_plan(prompt: str, fmt: str, opts: dict, context_ids: list) -> Optional[dict]:
    from app.services.ai_client import llm_generate

    catalogue = [{"id": p["id"], "name": p["name"], "category": p["category"], "rounds": p["rounds"]} for p in opts["products"]]
    ask = f"""{DESIGN_BRIEF if fmt == 'pptx' else ''}
Plan the story of the deck: lead with the headline results, then what customers like and dislike, then actions.
You turn a request for a {'PowerPoint' if fmt == 'pptx' else 'Excel'} consumer-feedback report into a JSON plan.
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
"compare rounds" or "compare surveys" -> compare_rounds true and rounds "all" (people call rounds "Survey 1",
"Survey 2": "survey 2" means round 2); audience is "internal", "external" or null; focus is "positive", "negative" or null."""
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
    # Rounds are called "Survey 1, Survey 2" on screen: both words name them.
    rounds_found = [int(n) for n in re.findall(r"\b(?:round|survey)\s*(\d+)\b", text)]
    compare = bool(re.search(r"\bcompar|\bvs\b|\bversus\b", text)) and not named[1:] and not every
    plan = {
        "title": "", "product_ids": ids,
        "rounds": rounds_found or ("all" if compare or re.search(r"\ball (the )?(rounds|surveys)\b", text) else "latest"),
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
    prompt = request_text(prompt)  # the design brief alone = the standard report
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
    parts.append("latest survey" if r == "latest" else "all surveys" if r == "all" else "survey " + ", ".join(map(str, r)))
    if plan["compare_rounds"]:
        parts.append("surveys compared")
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
            label = product.name + (f" · Survey {group[0].number}" if len(group) == 1 else
                                    f" · Surveys {', '.join(str(r.number) for r in group)}" if group else "")
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
        table(wb.create_sheet("Results"), ["Product / survey", "Responses", "Average rating", "Positive", "Mixed", "Negative", "Top like", "Top concern"], rows)
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
        table(wb.create_sheet("Findings"), ["Product / survey", "Kind", "Finding", "Customers", "Of responses", "Customer's words"], rows, wide=("Finding", "Customer's words"))
    if "ratings" in sec:
        rows = []
        for b in blocks:
            for star, count in sorted(((b["stats"] or {}).get("rating_distribution") or {}).items()):
                rows.append([b["label"], f"{star}★", count])
        table(wb.create_sheet("Ratings"), ["Product / survey", "Rating", "Responses"], rows)
    if "questions" in sec:
        rows = []
        for b in blocks:
            for q in (b["stats"] or {}).get("questions") or []:
                if q.get("distribution"):
                    rows += [[b["label"], q.get("question"), opt, cnt, q.get("answered")] for opt, cnt in q["distribution"].items()]
                elif q.get("average") is not None:
                    rows.append([b["label"], q.get("question"), "Average", q.get("average"), q.get("answered")])
        table(wb.create_sheet("Questions"), ["Product / survey", "Question", "Answer", "Count", "Answered"], rows, wide=("Question",))
    if "breakdowns" in sec:
        rows = []
        for b in blocks:
            for dim in plan["breakdowns"]:
                for g in ((b["stats"] or {}).get("breakdowns") or {}).get(dim) or []:
                    rows.append([b["label"], DIMENSIONS[dim], g.get("group"), g.get("responses"), g.get("avg_rating"), g.get("positive"), g.get("mixed"), g.get("negative")])
        table(wb.create_sheet("Breakdowns"), ["Product / survey", "Dimension", "Group", "Responses", "Average rating", "Positive", "Mixed", "Negative"], rows)
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
        table(wb.create_sheet("Surveys"), ["Product / survey", "Response #", "Date", "Survey person", "City", "Country", "Respondent type",
                                           "Internal / External", "Rating", "Sentiment", "Summary"], rows, wide=("Summary",))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- PowerPoint

def build_pptx(db: Session, plan: dict, blocks: list) -> bytes:
    """A designed deck (services/pptx_design.py): cover, comparison, one section per product
    (key results, likes, concerns, actions, recipe suggestions, breakdowns, questions) and
    key takeaways. Every number comes from the surveys; max_slides keeps the most important."""
    from app.services.pptx_design import GOLD, MIX, MUTED, NAVY, NEG, POS, SKY, Deck, pct, stars

    opts = {p.id: p.name for p in db.query(Product).filter(Product.id.in_(plan["product_ids"])).all()}
    sec = set(plan["sections"])
    many = len(blocks) > 1
    deck = Deck(footer=plan["title"])
    slides = []  # (priority, builder): lower number = kept first when max_slides cuts; order = list order

    total = sum(_kpi(b)[0] for b in blocks)
    rated = [(b, _kpi(b)) for b in blocks if _kpi(b)[1] is not None]
    avg = round(sum(k[1] * k[0] for _, k in rated) / max(1, sum(k[0] for _, k in rated)), 1) if rated else None
    positive = sum(_kpi(b)[2] for b in blocks)

    # Cover line: the products, the rounds and any filters (no technical details)
    names = list(dict.fromkeys(b["product"].name for b in blocks)) or [opts.get(i, f"#{i}") for i in plan["product_ids"]]
    scope = [p for p in describe(plan, opts).split(" · ")[1:]
             if not p.startswith(("sections:", "up to ")) and p != ", ".join(opts.get(i, f"#{i}") for i in plan["product_ids"][:3])]
    cover_line = " · ".join([", ".join(names[:4]) + (f" +{len(names) - 4} more" if len(names) > 4 else "")] + [p for p in scope if "#" not in p])

    def cover():
        deck.cover(plan["title"], cover_line,
                   [("surveys analysed", str(total)), ("average rating", f"{avg if avg is not None else '–'} / 5"),
                    ("positive customers", pct(positive, total))])
    slides.append((0, cover))

    if many and ("comparison" in sec or "kpis" in sec):
        def compare():
            s = deck.slide("Comparison", "How the products compare", f"{len(blocks)} products / surveys · numbers from the responses")
            ranked = sorted(blocks, key=lambda b: -(_kpi(b)[1] or 0))
            deck.bar_chart(s, 0.6, 1.95, 6.3, 4.85, [b["label"] for b in ranked[:8]], [_kpi(b)[1] for b in ranked[:8]],
                           horizontal=True, max_value=5.4, colors=[GOLD if i == 0 else NAVY for i in range(min(8, len(ranked)))])
            deck.table(s, 7.2, 2.05, 5.55, ["Product / survey", "Responses", "Rating", "Positive"],
                       [[b["label"][:36], _kpi(b)[0], _kpi(b)[1], pct(_kpi(b)[2], _kpi(b)[0])] for b in ranked],
                       col_w=[2.55, 0.95, 0.95, 1.1], max_rows=9)
        slides.append((1, compare))

    for b in blocks:
        n, rating, pos, mix, neg = _kpi(b)
        name, chip = b["product"].name, f"{n} surveys"
        nar = b["narrative"] or {}
        if many:
            slides.append((9, lambda b=b, n=n: deck.divider("Product", b["product"].name, f"{b['label']} · {n} surveys")))
        if "kpis" in sec or "summary" in sec:
            def kpis(b=b, n=n, rating=rating, pos=pos, mix=mix, neg=neg, name=name, chip=chip, nar=nar):
                s = deck.slide("Key results", f"{name} at a glance", b["label"], chip)
                deck.kpi(s, 0.6, 1.95, 2.9, 1.35, str(n), "Surveys", NAVY, "customers heard")
                deck.kpi(s, 3.7, 1.95, 2.9, 1.35, f"{rating if rating is not None else '–'}", "Average rating", GOLD,
                         f"{stars(rating)}  out of 5" if rating is not None else "")
                deck.kpi(s, 6.8, 1.95, 2.9, 1.35, pct(pos, n), "Positive", POS, f"{pos} of {n} customers")
                deck.kpi(s, 9.9, 1.95, 2.85, 1.35, pct(neg, n), "Negative", NEG, f"{neg} of {n} customers")
                dist = (b["stats"] or {}).get("rating_distribution") or {}
                if dist:
                    keys = sorted(dist, key=lambda k: float(k))
                    deck.text(s, 0.6, 3.5, 6, 0.3, "RATING DISTRIBUTION", 10, True, MUTED)
                    deck.bar_chart(s, 0.5, 3.75, 6.2, 2.75, [f"{k}★" for k in keys], [dist[k] for k in keys], number_format="0",
                                   colors=[NEG if float(k) <= 2 else (MIX if float(k) < 4 else POS) for k in keys])
                if n:
                    deck.text(s, 7.0, 3.5, 5.6, 0.3, "SENTIMENT", 10, True, MUTED)
                    deck.donut(s, 6.9, 3.7, 2.9, 2.85, ["Positive", "Mixed", "Negative"], [pos, mix, neg], [POS, MIX, NEG])
                conclusion = nar.get("conclusion")
                deck.callout(s, 9.95, 3.75, 2.8, 2.75, "Insight",
                             (conclusion or f"{pct(pos, n)} of customers were positive about {name}.")[:230])
            slides.append((2, kpis))
        if "likes" in sec and plan.get("focus") != "negative":
            def likes(b=b, n=n, name=name, chip=chip):
                s = deck.slide("What customers like", f"Why customers like {name}", b["label"], chip)
                y = 1.95
                for label, count, quote in _likes(b, 5) or [("Nothing stood out yet", 0, "")]:
                    y += deck.meter_card(s, 0.6, y, 12.15, label, count, n, quote, "pos") + 0.12
                    if y > 6.4:
                        break
            slides.append((4 if plan.get("focus") != "positive" else 3, likes))
        if "concerns" in sec and plan.get("focus") != "positive":
            def concerns(b=b, n=n, name=name, chip=chip):
                s = deck.slide("Concerns", f"What to improve in {name}", b["label"], chip)
                y = 1.95
                for label, count, quote in _concerns(b, 5) or [("No concerns raised", 0, "")]:
                    y += deck.meter_card(s, 0.6, y, 12.15, label, count, n, quote, "neg") + 0.12
                    if y > 6.4:
                        break
            slides.append((3 if plan.get("focus") == "negative" else 4, concerns))
        if "recommendations" in sec:
            def actions(b=b, name=name, chip=chip):
                s = deck.slide("Recommended actions", "What to do next", b["label"], chip)
                items = _actions(b)[:6] or ["No action needed yet: keep collecting feedback."]
                for i, a in enumerate(items):
                    col, row = i % 2, i // 2
                    deck.numbered(s, 0.6 + col * 6.15, 1.95 + row * 1.05, 6.0, i + 1, a)
            slides.append((5, actions))
        recs = nar.get("recipe_recommendations") or []
        if "recipe_recommendations" in sec and recs:
            def recipe(b=b, recs=recs, name=name, chip=chip):
                s = deck.slide("Recipe recommendations", f"Recipe changes to test for {name}",
                               f"{b['label']} · suggestions only: the recipe is never changed automatically", chip)
                y = 1.95
                for i, x in enumerate(recs[:5]):
                    who = f" · {x.get('customers')} customers" if x.get("customers") else ""
                    y += deck.numbered(s, 0.6, y, 12.15, i + 1, str(x.get("change") or ""), f"Why: {x.get('reason') or ''}{who}") + 0.12
            slides.append((6, recipe))
        if "breakdowns" in sec:
            for dim in plan["breakdowns"]:
                groups = [g for g in ((b["stats"] or {}).get("breakdowns") or {}).get(dim) or [] if g.get("group") != "Not given"]
                if not groups:
                    continue
                def breakdown(b=b, dim=dim, groups=groups, name=name, chip=chip):
                    s = deck.slide("Breakdown", f"{name} by {DIMENSIONS[dim].lower()}", b["label"], chip)
                    top = sorted(groups, key=lambda g: -(g.get("responses") or 0))[:8]
                    deck.text(s, 0.6, 1.95, 6, 0.3, "AVERAGE RATING", 10, True, MUTED)
                    deck.bar_chart(s, 0.5, 2.2, 6.4, 4.55, [g.get("group") for g in top], [g.get("avg_rating") for g in top],
                                   horizontal=True, max_value=5.4, color=SKY)
                    deck.table(s, 7.2, 2.05, 5.55, [DIMENSIONS[dim], "Surveys", "Positive", "Negative"],
                               [[str(g.get("group"))[:24], g.get("responses"), g.get("positive"), g.get("negative")] for g in top],
                               col_w=[2.35, 1.0, 1.1, 1.1], max_rows=9)
                slides.append((7, breakdown))
        if "questions" in sec:
            for q in [q for q in (b["stats"] or {}).get("questions") or [] if q.get("distribution")][:4]:
                def question(b=b, q=q, chip=chip):
                    s = deck.slide("Question", str(q.get("question"))[:80], b["label"], chip)
                    items = list(q["distribution"].items())[:8]
                    deck.bar_chart(s, 0.5, 1.95, 8.6, 4.85, [k for k, _ in items], [v for _, v in items], horizontal=True,
                                   number_format="0", colors=[GOLD if v == max(x for _, x in items) else NAVY for _, v in items])
                    best = max(items, key=lambda kv: kv[1])
                    deck.callout(s, 9.4, 2.05, 3.35, 2.2, "Most common answer",
                                 f"“{best[0]}” · {best[1]} of {q.get('answered') or sum(v for _, v in items)} customers")
                slides.append((8, question))

    def takeaways():
        s = deck.slide("Summary", "Key takeaways", plan["title"])
        best = max(blocks, key=lambda b: _kpi(b)[1] or 0) if blocks else None
        like = next((x for b in blocks for x in _likes(b, 1)), None)
        concern = next((x for b in blocks for x in _concerns(b, 1)), None)
        action = next((a for b in blocks for a in _actions(b)), None)
        cards = [("Strength", POS, like[0] if like else "Collect more feedback", f"{like[1]} customers mention it" if like else ""),
                 ("Watch out", NEG, concern[0] if concern else "No concerns raised", f"{concern[1]} customers mention it" if concern else ""),
                 ("Next step", GOLD, (action or "Keep collecting feedback")[:90], "")]
        for i, (label, color, head, note) in enumerate(cards):
            x = 0.6 + i * 4.12
            deck.kpi(s, x, 2.0, 3.92, 3.6, "", label, color)
            deck.text(s, x + 0.3, 2.6, 3.4, 2.0, head, 18, True, NAVY)
            if note:
                deck.text(s, x + 0.3, 4.9, 3.4, 0.4, note, 11, False, color)
        if best is not None and _kpi(best)[1] is not None:
            deck.callout(s, 0.6, 5.85, 12.15, 0.95, "Best rated", f"{best['label']}: {_kpi(best)[1]} / 5 from {_kpi(best)[0]} surveys")
    slides.append((2, takeaways))

    limit = plan.get("max_slides")
    order = sorted(range(len(slides)), key=lambda i: (slides[i][0], i))
    keep = set(order[:limit]) if limit else set(order)
    for i, (_, build) in enumerate(slides):
        if i in keep:
            build()
    buf = io.BytesIO()
    deck.save(buf)
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