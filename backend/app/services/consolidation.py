"""
Consolidated product report.

Combines the individual analyses already stored for one product. Nothing is
re-uploaded or re-transcribed.

Two layers:
  1. Statistics computed by code (exact, cannot be invented): ratings, sentiment
     distribution, how many customers mentioned each aspect, survey question
     results, breakdowns by gender / age group / location / usage period.
  2. Wording written by Qwen from those statistics: conclusion, themes,
     suggestions, actions. Counts it returns are checked against the real totals.
     If Qwen is unavailable, a plain report is written from the statistics.

Personal data (names, mobiles, addresses) is never sent to the AI.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter, defaultdict
from statistics import median

# ------------------------------------------------------------
# Tone of one customer's feedback
# ------------------------------------------------------------

POSITIVE_ITEM = {"positive"}
CONCERN_ITEM = {"negative", "positive with concerns"}


def feedback_items(chapters) -> list[dict]:
    """All structured feedback items (aspect, sentiment, rating, evidence) of one recording."""
    items = []
    for chapter in chapters or []:
        if isinstance(chapter, dict):
            for item in chapter.get("feedback") or []:
                if isinstance(item, dict) and (item.get("aspect_key") or item.get("aspect") or item.get("aspect_name")):
                    items.append(item)
    return items


def response_tone(overall_sentiment: str | None, chapters, rating=None) -> str:
    """
    positive | mixed | negative | neutral for one customer.

    The rating decides first; the aspects decide whether it's clear-cut. A small
    "slightly sweet" remark in otherwise glowing feedback is still positive.
    "Positive with concerns" counts as half a concern.
    """
    items = feedback_items(chapters)
    pos = sum(1 for i in items if str(i.get("sentiment", "")).lower() == "positive")
    neg = sum(
        1.0 if str(i.get("sentiment", "")).lower() == "negative" else 0.5
        for i in items
        if str(i.get("sentiment", "")).lower() in CONCERN_ITEM
    )
    try:
        r = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        r = None

    if r is not None and r > 0:
        if r >= 4:
            return "positive" if neg < pos or not neg else "mixed"
        if r < 2.5:
            return "negative" if pos <= neg / 2 else "mixed"
        return "mixed"

    label = (overall_sentiment or "").strip().lower()
    if label in ("praise", "positive"):
        return "positive" if neg < pos or not neg else "mixed"
    if label in ("issue", "negative"):
        return "negative" if pos <= neg / 2 else "mixed"
    if label in ("suggestion", "mixed"):
        return "mixed"
    if pos and neg:
        return "mixed"
    return "positive" if pos else "negative" if neg else "neutral"


# ------------------------------------------------------------
# 1. Statistics (code)
# ------------------------------------------------------------

def _age_group(age) -> str:
    try:
        age = int(age)
    except (TypeError, ValueError):
        return "Not given"
    if age < 25:
        return "Under 25"
    if age < 35:
        return "25–34"
    if age < 45:
        return "35–44"
    if age < 55:
        return "45–54"
    return "55 and over"


def _city(location: str | None) -> str:
    # "Andheri, Mumbai" -> "Mumbai": group by city, not by locality.
    if not location:
        return "Not given"
    parts = [p.strip() for p in str(location).split(",") if p.strip()]
    return parts[-1].title() if parts else "Not given"


USAGE_LABELS = {
    "last_week": "In the last week",
    "last_month": "In the last month",
    "last_3_months": "In the last 3 months",
    "more_than_3_months": "More than 3 months ago",
}


def _breakdown(items: list[dict], key) -> list[dict]:
    groups = defaultdict(list)
    for it in items:
        groups[key(it)].append(it)
    out = []
    for group, members in groups.items():
        ratings = [m["rating"] for m in members if m["rating"] is not None]
        tones = Counter(m["tone"] for m in members)
        out.append({
            "group": group,
            "responses": len(members),
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "positive": tones.get("positive", 0),
            "mixed": tones.get("mixed", 0),
            "negative": tones.get("negative", 0),
        })
    return sorted(out, key=lambda g: (-g["responses"], str(g["group"])))


# ------------------------------------------------------------
# Client summary: the classic product-test report (preference by
# location, price willingness, score, improvement areas), built from
# questions the admin tagged with a "report role" in the Catalog.
# ------------------------------------------------------------

def _pct(count: int, total: int) -> int:
    return round(100 * count / total) if total else 0


def _rows(counter: Counter, order: list, total: int) -> list:
    """Every option with count and %; catalog order first, then anything else answered."""
    keys = [o for o in order if o] + [k for k in counter if k not in order]
    return [{"option": k, "count": counter.get(k, 0), "pct": _pct(counter.get(k, 0), total)} for k in keys]


def _role_answers(items: list[dict], role: str):
    """(question, [(item, value)]) for the first question tagged with this role."""
    first = None
    for it in items:
        for a in it["answers"]:
            if a.get("report_role") == role and (a.get("value") or "").strip():
                if first is None or (a.get("position") or 0) < (first.get("position") or 0):
                    first = a
    if first is None:
        return None, []
    qid = first["question_id"]
    pairs = [(it, a["value"].strip()) for it in items for a in it["answers"] if a["question_id"] == qid and (a.get("value") or "").strip()]
    return first, pairs


def build_client_summary(items: list[dict]) -> dict:
    summary = {"households": len(items)}

    # A. Preference: this product (first option / "Yes") vs the alternative, overall and per location.
    q, pairs = _role_answers(items, "preference")
    if q:
        order = (q.get("options") or []) or ["Yes", "No"]
        total = len(pairs)
        overall = Counter(v for _, v in pairs)
        by_place = defaultdict(Counter)
        for it, v in pairs:
            by_place[_city(it.get("survey_location") or it.get("location"))][v] += 1
        ours = order[0]
        summary["preference"] = {
            "question": q["question_text"],
            "this_product": ours,
            "options": [o for o in order],
            "answered": total,
            "overall": _rows(overall, order, total),
            "preferred_pct": _pct(overall.get(ours, 0), total),
            "by_location": sorted(
                (
                    {"location": place, "households": sum(c.values()), "rows": _rows(c, order, sum(c.values()))}
                    for place, c in by_place.items()
                ),
                key=lambda r: -r["households"],
            ),
        }

    # C. Price willingness: every price point, and the most chosen one.
    q, pairs = _role_answers(items, "price")
    if q:
        total = len(pairs)
        counts = Counter(v for _, v in pairs)
        rows = _rows(counts, q.get("options") or [], total)
        top = max(rows, key=lambda r: r["count"]) if rows else None
        summary["price"] = {"question": q["question_text"], "answered": total, "rows": rows,
                            "ideal": top["option"] if top and top["count"] else None, "ideal_pct": top["pct"] if top else 0}

    # D. Overall score: distribution, average (if numeric) and the share giving the top two scores.
    q, pairs = _role_answers(items, "score")
    if q:
        total = len(pairs)
        counts = Counter(v for _, v in pairs)
        order = [str(k) for k in range(1, 6)] if q["question_type"] == "rating" else (q.get("options") or [])
        rows = _rows(counts, order, total)
        nums = []
        for v, c in counts.items():
            m = re.search(r"\d+(\.\d+)?", v)
            if m:
                nums += [float(m.group())] * c
        top_two = rows[-2:] if len(rows) >= 2 else rows
        summary["score"] = {
            "question": q["question_text"], "answered": total, "rows": rows,
            "scale": "1-5" if q["question_type"] == "rating" else None,
            "average": round(sum(nums) / len(nums), 2) if nums else None,
            "top_two_pct": _pct(sum(r["count"] for r in top_two), total),
            "top_two_labels": [r["option"] for r in top_two],
        }

    # E. Improvement areas: most requested first (text answers are grouped by the AI wording).
    q, pairs = _role_answers(items, "improvement")
    if q:
        total = len(pairs)
        if q["question_type"] == "text":
            summary["improvement"] = {"question": q["question_text"], "question_id": q["question_id"], "answered": total, "rows": [], "from_text": True}
        else:
            counts = Counter(v for _, v in pairs)
            rows = sorted(_rows(counts, q.get("options") or [], total), key=lambda r: -r["count"])
            summary["improvement"] = {"question": q["question_text"], "answered": total, "rows": rows, "from_text": False}

    # Purchase intent and ease of use/preparation: simple distributions.
    for role in ("purchase_intent", "ease"):
        q, pairs = _role_answers(items, role)
        if q:
            total = len(pairs)
            counts = Counter(v for _, v in pairs)
            order = [str(k) for k in range(1, 6)] if q["question_type"] == "rating" else ((q.get("options") or []) or ["Yes", "No"])
            rows = _rows(counts, order, total)
            summary[role] = {"question": q["question_text"], "answered": total, "rows": rows,
                             "positive_pct": rows[0]["pct"] if role == "purchase_intent" and rows else None}
    return summary


def build_stats(items: list[dict]) -> dict:
    """
    items: one dict per analyzed response:
      rating, tone, aspects [{key, name, sentiment, rating, evidence}],
      answers [{question_id, position, question_text, question_type, value}],
      gender, age, location, usage_period
    """
    n = len(items)
    ratings = [i["rating"] for i in items if i["rating"] is not None]
    tones = Counter(i["tone"] for i in items)

    # ---- aspects: counted once per customer, not per sentence ----
    aspects = {}
    for idx, it in enumerate(items):
        seen = defaultdict(set)
        for a in it["aspects"]:
            key = a["key"]
            entry = aspects.setdefault(key, {
                "key": key, "name": a["name"], "customers": set(), "positive": set(),
                "negative": set(), "neutral": set(), "ratings": [], "evidence": [],
            })
            entry["customers"].add(idx)
            s = str(a.get("sentiment", "")).lower()
            bucket = "positive" if s in POSITIVE_ITEM else "negative" if s in CONCERN_ITEM else "neutral"
            entry[bucket].add(idx)
            if a.get("rating") is not None:
                entry["ratings"].append(float(a["rating"]))
            ev = (a.get("evidence") or "").strip()
            if ev and len(entry["evidence"]) < 4 and ev not in entry["evidence"] and idx not in seen[key]:
                entry["evidence"].append(ev)
                seen[key].add(idx)

    aspect_list = []
    for e in aspects.values():
        aspect_list.append({
            "key": e["key"],
            "name": e["name"],
            "customers": len(e["customers"]),
            "positive": len(e["positive"]),
            "negative": len(e["negative"]),
            "neutral": len(e["neutral"] - e["positive"] - e["negative"]),
            "avg_rating": round(sum(e["ratings"]) / len(e["ratings"]), 2) if e["ratings"] else None,
            "evidence": e["evidence"],
        })
    aspect_list.sort(key=lambda a: (-a["customers"], -a["negative"], a["name"]))

    # ---- survey questions ----
    questions = {}
    for it in items:
        for ans in it["answers"]:
            q = questions.setdefault(ans["question_id"], {
                "question_id": ans["question_id"], "position": ans.get("position") or 0,
                "question": ans["question_text"], "type": ans["question_type"],
                "answered": 0, "values": [],
            })
            value = (ans.get("value") or "").strip()
            if value:
                q["answered"] += 1
                q["values"].append(value)

    question_list = []
    for q in sorted(questions.values(), key=lambda q: (q["position"], q["question_id"])):
        out = {k: q[k] for k in ("question_id", "question", "type", "answered")}
        if q["type"] == "rating":
            nums = [int(v) for v in q["values"] if v.isdigit() and 1 <= int(v) <= 5]
            out["average"] = round(sum(nums) / len(nums), 2) if nums else None
            out["distribution"] = {str(k): nums.count(k) for k in range(1, 6)}
        elif q["type"] in ("yes_no", "single_choice"):
            out["distribution"] = dict(Counter(q["values"]).most_common())
        else:
            out["text_answers"] = q["values"][:50]
        question_list.append(out)

    return {
        "response_count": n,
        "overall_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "median_rating": round(median(ratings), 2) if ratings else None,
        "rated_responses": len(ratings),
        "rating_distribution": {
            str(k): sum(1 for r in ratings if round(r) == k) for k in range(1, 6)
        },
        "sentiment_distribution": {
            "positive": tones.get("positive", 0),
            "mixed": tones.get("mixed", 0),
            "negative": tones.get("negative", 0),
            "neutral": tones.get("neutral", 0),
        },
        "aspects": aspect_list,
        "questions": question_list,
        "client_summary": build_client_summary(items),
        "breakdowns": {
            "gender": _breakdown(items, lambda i: (i.get("gender") or "Not given").title()),
            "age_group": _breakdown(items, lambda i: _age_group(i.get("age"))),
            "city": _breakdown(items, lambda i: _city(i.get("location"))),
            "usage_period": _breakdown(items, lambda i: USAGE_LABELS.get(i.get("usage_period"), "Not given")),
        },
    }


# ------------------------------------------------------------
# 2. Wording
# ------------------------------------------------------------

def fallback_narrative(product_name: str, stats: dict) -> dict:
    """A plain report written from the statistics alone (used if Qwen is unavailable)."""
    n = stats["response_count"]
    aspects = stats["aspects"]
    pos = sorted([a for a in aspects if a["positive"]], key=lambda a: -a["positive"])
    neg = sorted([a for a in aspects if a["negative"]], key=lambda a: -a["negative"])
    neutral = [a for a in aspects if a["neutral"] and not a["positive"] and not a["negative"]]
    sd = stats["sentiment_distribution"]

    def ev(a):
        return f' ("{a["evidence"][0]}")' if a["evidence"] else ""

    parts = [f"{n} customers gave feedback on {product_name}."]
    if stats["overall_rating"] is not None:
        parts.append(f"The average rating is {stats['overall_rating']:.1f} out of 5.")
    parts.append(f"{sd['positive']} were positive, {sd['mixed']} mixed and {sd['negative']} negative.")
    if pos:
        parts.append(f"Most praised: {pos[0]['name']} ({pos[0]['positive']} of {n}).")
    if neg:
        parts.append(f"Most common concern: {neg[0]['name']} ({neg[0]['negative']} of {n}).")

    return {
        "conclusion": " ".join(parts),
        "common_positives": [{"point": f"{a['name']}{ev(a)}", "customers": a["positive"]} for a in pos[:6]],
        "recurring_issues": [{"point": f"{a['name']}{ev(a)}", "customers": a["negative"]} for a in neg if a["negative"] >= 2][:6]
        or [{"point": f"{a['name']}{ev(a)}", "customers": a["negative"]} for a in neg[:3]],
        "neutral_feedback": [a["name"] for a in neutral[:5]],
        "themes": [
            {"theme": a["name"], "customers": a["customers"],
             "sentiment": "positive" if a["positive"] > a["negative"] else "negative" if a["negative"] > a["positive"] else "mixed"}
            for a in aspects[:8]
        ],
        "suggestions": [],
        "recommended_actions": [f"Address {a['name'].lower()}, raised by {a['negative']} of {n} customers." for a in neg[:3]],
        "generated_by": "statistics",
    }


# ------------------------------------------------------------
# 2. AI wording: fast
# ------------------------------------------------------------
#
# Speed on a CPU depends mostly on how much text the model has to write.
# So: translations are cached in the database and done ahead of time, the whole
# report is ONE short request, and the model stays loaded between requests.

# Optional faster model just for reports, e.g. in PowerShell before uvicorn:
#   $env:FMCG_REPORT_MODEL = "qwen2.5:3b"     (run "ollama pull qwen2.5:3b" once)
REPORT_MODEL = os.environ.get("FMCG_REPORT_MODEL", "").strip() or None

# Keep the model in memory so the next request doesn't wait for it to load.
KEEP_ALIVE = os.environ.get("FMCG_OLLAMA_KEEP_ALIVE", "30m")


def _ollama(prompt: str, num_predict: int, json_mode: bool = True, timeout: int = 300) -> str:
    import requests

    from app.services.ollama_service import MODEL, OLLAMA_URL

    payload = {
        "model": REPORT_MODEL or MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0.1, "num_predict": num_predict, "num_ctx": 4096},
    }
    if json_mode:
        payload["format"] = "json"
    r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json().get("response", "").strip()


def _parse_json(text: str):
    """JSON object from a model reply, tolerating fences or text around it."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("the reply contained no JSON")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


# ---------- Translation (cached, done ahead of time) ----------

_ENGLISH_WORDS = set("""
a an the and or but if so to of in on at for with from by about as is are was were be been it its this that
these those i we you he she they me my our your their would could should will can may might more less
very too much many some any not no make made want like liked good better bad price taste flavour flavor
sweet sweetness packaging pack quality little bit slightly also just only than then easy easier need
""".split())


def looks_english(text: str) -> bool:
    words = re.findall(r"[A-Za-z']+", text or "")
    if not words or any(ord(ch) > 127 for ch in text if ch.isalpha()):
        return False
    return sum(1 for w in words if w.lower() in _ENGLISH_WORDS) / len(words) >= 0.3


BATCH_TRANSLATE_PROMPT = """Translate each customer answer below into natural, simple English.
They may be in Hindi, Marathi, Gujarati, Tamil, Telugu, Bengali, Kannada, Malayalam, Punjabi or
Urdu, written in that script or in Latin letters (like "thodo vadhu strong joiye").

{answers}

Return JSON: {{"translations": ["English of answer 1", "English of answer 2", ...]}}
Exactly {n} translations, in the same order. English only."""

SINGLE_TRANSLATE_PROMPT = """Translate this customer answer into natural, simple English.
Reply with the English only.

Answer: {text}
English:"""


def _clean_translation(text: str) -> str:
    text = re.sub(r"^(english|translation)\s*:\s*", "", (text or "").strip(), flags=re.I).strip()
    text = text.splitlines()[0].strip() if text else ""
    return text.strip(' "“”\'')


def translate_many(texts: list[str]) -> list:
    """Many answers -> English in one request; one by one only if that fails."""
    if not texts:
        return []
    try:
        raw = _parse_json(_ollama(
            BATCH_TRANSLATE_PROMPT.format(n=len(texts), answers="\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))),
            num_predict=60 * len(texts) + 80,
        ))
        out = raw.get("translations")
        if isinstance(out, list) and len(out) == len(texts):
            cleaned = [_clean_translation(str(t)) or None for t in out]
            if all(cleaned):
                return cleaned
    except Exception as e:
        print(f"Translation: batch request failed ({e}); translating one by one.")
    result = []
    for t in texts:
        try:
            result.append(_clean_translation(_ollama(SINGLE_TRANSLATE_PROMPT.format(text=t), 160, json_mode=False)) or None)
        except Exception as e:
            print(f"Translation failed: {e}")
            result.append(None)
    return result


def ensure_translations(db, response_ids=None, product_id=None, limit: int = 40) -> int:
    """
    Fill survey_answers.value_en for free-text answers that don't have it yet.
    English answers are copied; the rest are translated in one request per batch.
    Returns how many answers were translated.
    """
    from app.database.models import SurveyAnswer, SurveyResponse

    q = db.query(SurveyAnswer).filter(
        SurveyAnswer.question_type == "text",
        SurveyAnswer.value_en.is_(None),
        SurveyAnswer.value.isnot(None),
    )
    if response_ids is not None:
        q = q.filter(SurveyAnswer.response_id.in_(response_ids))
    if product_id is not None:
        q = q.join(SurveyResponse, SurveyResponse.id == SurveyAnswer.response_id).filter(SurveyResponse.product_id == product_id)
    rows = [r for r in q.limit(limit).all() if (r.value or "").strip()]
    if not rows:
        return 0

    pending = []
    for r in rows:
        if looks_english(r.value):
            r.value_en = r.value.strip()
        else:
            pending.append(r)
    done = 0
    for start in range(0, len(pending), 10):
        batch = pending[start:start + 10]
        for row, english in zip(batch, translate_many([r.value.strip() for r in batch])):
            if english:
                row.value_en = english
                done += 1
    db.commit()
    return done


def translate_pending_in_background(product_id=None):
    """Run after a page loads, so translations are ready before Generate is pressed."""
    from app.database.database import SessionLocal

    db = SessionLocal()
    try:
        started = time.time()
        n = ensure_translations(db, product_id=product_id)
        if n:
            print(f"Translated {n} survey answers to English in {time.time() - started:.0f}s (ready for reports).")
    except Exception as e:
        print(f"Background translation skipped: {e}")
    finally:
        db.close()


# ---------- The report: one short request ----------

PROMPT = """You are an FMCG consumer-insights analyst. Product: {product}. Customers surveyed: {n}.

EXACT STATISTICS (the only source of numbers):
{stats}

OPEN-ENDED ANSWERS (in English):
{open}

CUSTOMER SUMMARIES:
{summaries}

PRODUCT RECIPE (how it is made today):
{recipe}

Return JSON:
{{
  "conclusion": "3 sentences: verdict, what customers value, main problem and priority.",
  "common_positives": [{{"point": "short phrase", "customers": 0}}],
  "recurring_issues": [{{"point": "short phrase", "customers": 0}}],
  "neutral_feedback": ["short phrase"],
  "suggestions": ["short phrase"],
  "recommended_actions": ["short phrase"],
  "open_answers": [{{"question_id": 0, "summary": "1-2 sentences", "points": [{{"point": "short phrase", "customers": 0}}]}}],
  "recipe_recommendations": [{{"change": "specific change to an ingredient or step, with amounts", "ingredient": "name or empty", "step": "e.g. Step 3 or empty", "reason": "the customer problem", "customers": 0}}]
}}

Rules: "customers" comes from the statistics or from counting the answers; never more than {n}.
recipe_recommendations: only when a recipe is given; link each recurring problem to the ingredient or step
that causes it and suggest a concrete, moderate change (e.g. "reduce sugar from 450 g to about 340 g").
Leave it empty if there is no recipe.
At most 4 items per list. Phrases under 12 words. English only. No invented points."""


# Which ingredients usually drive which complaint (fallback when the AI is unavailable).
_INGREDIENT_HINTS = {
    "sweet": ["sugar", "syrup", "jaggery", "glucose", "honey", "sweetener"],
    "sugar": ["sugar", "syrup", "jaggery", "glucose", "honey", "sweetener"],
    "sour": ["citric", "acid", "lemon", "tamarind"],
    "salt": ["salt", "sodium"],
    "texture": ["gelatin", "gelatine", "pectin", "agar", "starch", "gum", "carrageenan"],
    "sticky": ["gelatin", "pectin", "agar", "starch", "gum", "glucose"],
    "colour": ["colour", "color", "dye", "tartrazine", "sunset"],
    "color": ["colour", "color", "dye", "tartrazine", "sunset"],
    "flavour": ["flavour", "flavor", "essence", "pulp", "extract"],
    "flavor": ["flavour", "flavor", "essence", "pulp", "extract"],
    "taste": ["pulp", "flavour", "flavor", "essence", "extract"],
    "smell": ["flavour", "flavor", "essence", "preservative"],
}


def recipe_fallback(stats: dict, recipe) -> list:
    """Recipe changes from keyword matches: recurring complaint -> matching ingredient."""
    if not recipe or not recipe.get("ingredients"):
        return []
    n = stats["response_count"]
    out = []
    for a in sorted(stats["aspects"], key=lambda x: -x["negative"]):
        if a["negative"] < 2:
            continue
        words = f'{a["key"]} {a["name"]}'.lower()
        hints = [h for k, hs in _INGREDIENT_HINTS.items() if k in words for h in hs]
        for ing in recipe.get("ingredients", []):
            name = str(ing.get("name") or "")
            if hints and any(h in name.lower() for h in hints):
                qty = " ".join(str(x) for x in (ing.get("quantity"), ing.get("unit")) if x)
                step = next(
                    (f"Step {i + 1}" for i, s in enumerate(recipe.get("steps", []))
                     if name.lower().split()[0] in f'{s.get("title", "")} {s.get("details", "")}'.lower()),
                    "",
                )
                out.append({
                    "change": f"Review the amount of {name}{f' (currently {qty})' if qty else ''} and test a lower level.",
                    "ingredient": name,
                    "step": step,
                    "reason": f"{a['name']}: concern for {a['negative']} of {n} customers",
                    "customers": a["negative"],
                })
                break
        if len(out) >= 4:
            break
    return out


def _recipe_text(recipe) -> str:
    if not recipe or not (recipe.get("ingredients") or recipe.get("steps")):
        return "(no recipe provided)"
    lines = []
    if recipe.get("batch_size"):
        lines.append(f"Batch: {recipe['batch_size']}")
    if recipe.get("ingredients"):
        lines.append("Ingredients:")
        for i in recipe["ingredients"][:40]:
            qty = " ".join(str(x) for x in (i.get("quantity"), i.get("unit")) if x)
            lines.append(f"- {i.get('name')}{f': {qty}' if qty else ''}{' (' + str(i.get('notes')) + ')' if i.get('notes') else ''}")
    if recipe.get("steps"):
        lines.append("Steps:")
        for k, st in enumerate(recipe["steps"][:40], start=1):
            extra = ", ".join(x for x in (st.get("temperature"), st.get("duration")) if x)
            lines.append(f"Step {k}. {st.get('title')}{f' [{extra}]' if extra else ''}: {(st.get('details') or '')[:200]}")
    if recipe.get("notes"):
        lines.append(f"Notes: {str(recipe['notes'])[:300]}")
    return "\n".join(lines)


def _clean_count(value, n: int):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if 0 < v <= n else None


def _counted(items, n, label="point", limit=4):
    out = []
    for item in items or []:
        if isinstance(item, dict):
            text = str(item.get(label) or "").strip()
            c = _clean_count(item.get("customers"), n)
            if text and c:
                out.append({label: text, "customers": c})
    return sorted(out, key=lambda x: -x["customers"])[:limit]


def _strings(items, limit=4):
    return [str(s).strip() for s in items or [] if isinstance(s, (str, int, float)) and str(s).strip()][:limit]


def _recipe_items(items, n: int) -> list:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        change = str(it.get("change") or "").strip()
        c = _clean_count(it.get("customers"), n)
        if change and c:
            out.append({
                "change": change,
                "ingredient": str(it.get("ingredient") or "").strip(),
                "step": str(it.get("step") or "").strip(),
                "reason": str(it.get("reason") or "").strip(),
                "customers": c,
            })
    return sorted(out, key=lambda x: -x["customers"])[:4]


def write_narrative(
    product_name: str, stats: dict, summaries: list[str], translations: dict | None = None, recipe: dict | None = None
) -> dict:
    """
    translations: original answer text -> English (from survey_answers.value_en).
    """
    translations = translations or {}
    n = stats["response_count"]
    fallback = fallback_narrative(product_name, stats)

    # Free-text questions, with English versions aligned to the original answers.
    open_qs = []
    for q in stats.get("questions", []):
        if q.get("type") == "text" and q.get("text_answers"):
            originals = q["text_answers"]
            english = [translations.get(a) or (a if looks_english(a) else None) for a in originals]
            open_qs.append({"question_id": q["question_id"], "question": q["question"], "originals": originals, "english": english})

    def open_answers_from(raw_list):
        by_id = {}
        for item in raw_list or []:
            if isinstance(item, dict):
                try:
                    by_id[int(item.get("question_id"))] = item
                except (TypeError, ValueError):
                    continue
        out = {}
        for i, q in enumerate(open_qs):
            answered = len([e for e in q["english"] if e]) or len(q["originals"])
            item = by_id.get(q["question_id"]) or (raw_list[i] if raw_list and i < len(raw_list) and isinstance(raw_list[i], dict) else {})
            entry = {
                "answered": answered,
                "summary": str(item.get("summary") or "").strip(),
                "points": _counted(item.get("points"), answered, limit=6),
                "translations": q["english"],
            }
            missing = sum(1 for e in q["english"] if not e)
            if missing:
                entry["error"] = f"{missing} of {len(q['originals'])} answers aren't translated yet."
            out[str(q["question_id"])] = entry
        return out

    compact = {
        "average_rating": stats["overall_rating"],
        "sentiment": stats["sentiment_distribution"],
        "aspects": [
            {"name": a["name"], "customers": a["customers"], "praised": a["positive"], "concerns": a["negative"],
             "example": (a["evidence"] or [""])[0][:80]}
            for a in stats["aspects"][:12]
        ],
        "questions": [
            {"question": q["question"], "average": q.get("average"), "answers": q.get("distribution")}
            for q in stats["questions"] if q.get("type") != "text"
        ],
    }
    open_text = "\n".join(
        f'question_id {q["question_id"]}: "{q["question"]}"\n' + "\n".join(f"- {e}" for e in q["english"] if e)
        for q in open_qs
    ) or "(none)"
    prompt = PROMPT.format(
        product=product_name,
        n=n,
        stats=json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
        open=open_text,
        summaries="\n".join(f"- {s[:160]}" for s in summaries[:30]) or "(none)",
        recipe=_recipe_text(recipe),
    )

    started = time.time()
    try:
        raw = _parse_json(_ollama(prompt, num_predict=950 if recipe else 700, timeout=600))
        result = {
            "conclusion": str(raw.get("conclusion") or "").strip() or fallback["conclusion"],
            "common_positives": _counted(raw.get("common_positives"), n) or fallback["common_positives"],
            "recurring_issues": _counted(raw.get("recurring_issues"), n) or fallback["recurring_issues"],
            "neutral_feedback": _strings(raw.get("neutral_feedback")) or fallback["neutral_feedback"],
            "themes": fallback["themes"],
            "suggestions": _strings(raw.get("suggestions")),
            "recommended_actions": _strings(raw.get("recommended_actions")) or fallback["recommended_actions"],
            "open_answers": open_answers_from(raw.get("open_answers")),
            "recipe_recommendations": _recipe_items(raw.get("recipe_recommendations"), n) if recipe else [],
            "generated_by": "ai",
        }
        if recipe and not result["recipe_recommendations"]:
            result["recipe_recommendations"] = recipe_fallback(stats, recipe)
        print(f"Consolidated report: AI wording written in {time.time() - started:.0f}s.")
    except Exception as e:
        print(f"Consolidated report: AI wording unavailable ({e}); using the statistics-based report.")
        result = dict(fallback, open_answers=open_answers_from([]), recipe_recommendations=recipe_fallback(stats, recipe))

    if not result.get("suggestions"):
        result["suggestions"] = [p["point"] for oa in result["open_answers"].values() for p in oa["points"]][:4]
    return result