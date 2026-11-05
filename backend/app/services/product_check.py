"""
Product check: is the recorded feedback about the product the survey person selected?

Runs after every analysis (api/video.py, services/analysis_worker.py) on the analyzed
text: the transcript plus the AI's English summary and parts, so a Hindi or Marathi
recording is still checked through its English summary. Products come from the
catalogue (nothing is hardcoded); aliases are built from each product's name:
  "Aam (Mango) Jelly"      -> "aam (mango) jelly", "aam jelly", "mango jelly"
  "Kellogg's Corn Flakes"  -> "kellogg's corn flakes", "kelloggs corn flakes", "corn flakes"
Distinctive words of a name ("chocolate", "jelly", "granola") are weak evidence:
they can make a result UNCERTAIN but never a MISMATCH on their own.

Outcomes
  MATCH      the selected product is named (and no other product clearly dominates)
             -> analyzed, in the reports
  MISMATCH   another product is named and the selected one is not
             -> survey status "needs_review": out of every report, count and round
                target until an admin confirms, corrects or marks it invalid
  UNCERTAIN  nothing named, or mixed mentions -> stays under the selected product
             (the survey person chose it), shown as "Unverified" for optional review

Reviews (review()): confirm / correct (moves the survey to the right product and
its current round; the original product and round stay on the check) / invalid
(excluded for good; the recording is kept). A reviewer's decision is never
overwritten by a later re-analysis. Every decision is in the audit log.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import FeedbackRecord, Product, ProductCheck, SurveyResponse

MATCH, MISMATCH, UNCERTAIN = "match", "mismatch", "uncertain"
NEEDS_REVIEW, INVALID = "needs_review", "invalid"  # survey statuses that keep it out of reports
STOP = {"the", "and", "of", "a", "an", "with", "in", "for", "mix", "pack", "new", "classic", "original", "flavour", "flavor"}


def _norm(text: str) -> str:
    text = (text or "").lower().replace("’", "'")
    return re.sub(r"[^a-z0-9'()\u0900-\u097f]+", " ", text).strip()


def aliases(name: str) -> set:
    """Phrases that name the product explicitly."""
    base = _norm(name)
    out = {base, base.replace("'", "")}
    inside = re.findall(r"\(([^)]+)\)", base)
    outside = re.sub(r"\([^)]*\)", " ", base).split()
    if inside:
        out.add(" ".join(outside))  # aam jelly
        for word in inside:  # mango jelly
            if outside:
                out.add(" ".join([word.strip()] + outside[1:]))
    words = [w for w in re.sub(r"[()]", " ", base).split()]
    if len(words) >= 3 and words[0].endswith("'s"):  # kellogg's corn flakes -> corn flakes
        out.add(" ".join(words[1:]))
    return {a.strip() for a in out if len(a.strip()) >= 4}


def _words(name: str) -> set:
    return {w.strip("'") for w in re.sub(r"[()]", " ", _norm(name)).split() if len(w) >= 4 and w not in STOP}


def _count(text: str, phrase: str) -> int:
    return len(re.findall(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text))


def analysis_text(record: Optional[FeedbackRecord], response: Optional[SurveyResponse]) -> str:
    parts = []
    if record is not None:
        parts += [record.transcript or "", record.overall_summary or ""]
        for ch in record.chapters or []:
            if isinstance(ch, dict):
                parts += [str(ch.get(k) or "") for k in ("title", "summary", "takeaway", "transcript")]
    if response is not None and isinstance(response.analysis, dict):
        parts.append(str(response.analysis.get("summary") or ""))
    return _norm(" ".join(parts))


def detect(db: Session, selected_id: int, text: str) -> dict:
    """{status, detected_product_id, confidence, evidence} for this text."""
    products = db.query(Product).filter(Product.is_active == 1).all()
    if not any(p.id == selected_id for p in products):
        extra = db.get(Product, selected_id)
        if extra is not None:
            products.append(extra)
    named, weak = {}, {}
    for p in products:
        named[p.id] = sum(_count(text, a) for a in aliases(p.name))
    # Distinctive words: in this product's name but in no other product's name.
    all_words = {p.id: _words(p.name) for p in products}
    for p in products:
        others = set().union(*[w for pid, w in all_words.items() if pid != p.id]) if len(products) > 1 else set()
        weak[p.id] = sum(_count(text, w) for w in all_words[p.id] - others)
    names = {p.id: p.name for p in products}
    # A product with exactly the selected product's name (e.g. in another category) is
    # not a rival: the transcript can't tell them apart.
    same = {p.id for p in products if _norm(p.name) == _norm(names.get(selected_id, ""))}
    for pid in same - {selected_id}:
        named[pid], weak[pid] = 0, 0
    sel_named = named.get(selected_id, 0)
    rivals = sorted(((n, pid) for pid, n in named.items() if pid != selected_id and n > 0), reverse=True)
    evidence = {
        "mentions": {names[pid]: n for pid, n in named.items() if n},
        "words": {names[pid]: n for pid, n in weak.items() if n},
    }
    if rivals and sel_named == 0:
        n, pid = rivals[0]
        return {"status": MISMATCH, "detected_product_id": pid, "confidence": round(min(0.95, 0.7 + 0.1 * n), 2), "evidence": evidence}
    if sel_named and rivals and rivals[0][0] >= 2 * sel_named:
        return {"status": UNCERTAIN, "detected_product_id": rivals[0][1], "confidence": 0.5, "evidence": evidence}
    if sel_named:
        return {"status": MATCH, "detected_product_id": selected_id, "confidence": round(min(0.98, 0.8 + 0.05 * sel_named), 2), "evidence": evidence}
    # Nothing named: weak words only.
    weak_rivals = sorted(((n, pid) for pid, n in weak.items() if pid != selected_id and n > 0), reverse=True)
    if weak.get(selected_id, 0) and not weak_rivals:
        return {"status": MATCH, "detected_product_id": selected_id, "confidence": 0.6, "evidence": evidence}
    if weak_rivals and weak_rivals[0][0] > weak.get(selected_id, 0):
        return {"status": UNCERTAIN, "detected_product_id": weak_rivals[0][1], "confidence": 0.4, "evidence": evidence}
    return {"status": UNCERTAIN, "detected_product_id": None, "confidence": 0.0, "evidence": evidence}


def run_check(db: Session, response: SurveyResponse, record: Optional[FeedbackRecord]) -> Optional[ProductCheck]:
    """Check an analyzed survey and set its status (call before commit). A reviewer's
    decision is kept: re-analysis doesn't reopen a reviewed survey."""
    if response is None or response.product_id is None:
        return None
    check = db.query(ProductCheck).filter(ProductCheck.response_id == response.id).first()
    if check is not None and check.review_status in ("confirmed", "corrected", "invalid"):
        if check.review_status == "invalid":
            response.status = INVALID
        return check
    found = detect(db, response.product_id, analysis_text(record, response))
    if check is None:
        check = ProductCheck(response_id=response.id, selected_product_id=response.product_id, selected_round_id=response.round_id)
        db.add(check)
    check.feedback_id = record.id if record is not None else None
    check.status = found["status"]
    check.detected_product_id = found["detected_product_id"]
    check.confidence = found["confidence"]
    check.evidence = found["evidence"]
    check.checked_at = datetime.utcnow()
    check.review_status = "pending" if found["status"] == MISMATCH else ("optional" if found["status"] == UNCERTAIN else "none")
    if found["status"] == MISMATCH:
        response.status = NEEDS_REVIEW
        _notify_mismatch(db, response, check)
    return check


def _notify_mismatch(db: Session, response: SurveyResponse, check: ProductCheck) -> None:
    try:
        from app.database.models import User
        from app.services.notifications import notify

        selected = db.get(Product, check.selected_product_id)
        detected = db.get(Product, check.detected_product_id) if check.detected_product_id else None
        sel, det = (selected.name if selected else "the selected product"), (detected.name if detected else "another product")
        data = {"response_id": response.id, "selected_product_id": check.selected_product_id, "detected_product_id": check.detected_product_id,
                "selected_product": sel, "detected_product": det}
        admins = [uid for (uid,) in db.query(User.id).filter(User.role == "admin", User.is_active == 1).all()]
        notify(db, admins, "product_mismatch", f"Product mismatch: survey #{response.id}",
               f"Selected {sel}, but the customer talks about {det}. It's kept out of the reports until you review it.",
               data=data, dedupe_key=f"product_mismatch:{response.id}:{check.checked_at.isoformat()}")
        if response.user_id:
            notify(db, [response.user_id], "product_mismatch", "Survey needs a product check",
                   f"Your survey #{response.id} was for {sel}, but the customer seems to talk about {det}. Your admin will review it.",
                   data=data, dedupe_key=f"product_mismatch_person:{response.id}:{check.checked_at.isoformat()}")
    except Exception as e:  # never lose the survey over a notification
        print(f"Product check notification failed: {e}", flush=True)


def review(db: Session, response: SurveyResponse, check: ProductCheck, action: str, reviewer, remarks: str = "",
           product_id: Optional[int] = None) -> list:
    """confirm | correct | invalid. Returns the product ids whose reports must be rebuilt."""
    from app.services.product_access import current_round_of

    touched = {response.product_id}
    now = datetime.utcnow()
    if action == "confirm":
        response.status = "analyzed"
        check.review_status = "confirmed"
    elif action == "correct":
        target = db.get(Product, int(product_id or 0))
        if target is None:
            raise ValueError("Choose the product the feedback is about.")
        if target.id == response.product_id:
            raise ValueError("That's already the survey's product. Use Confirm instead.")
        r = current_round_of(db, target.id)
        if r is None:
            from app.database.models import SurveyRound

            r = db.query(SurveyRound).filter(SurveyRound.product_id == target.id).order_by(SurveyRound.number.desc()).first()
        response.product_id = target.id
        response.round_id = r.id if r is not None else None
        response.recipe_version = r.recipe_version if r is not None else response.recipe_version
        response.status = "analyzed"
        check.review_status = "corrected"
        check.corrected_product_id = target.id
        touched.add(target.id)
    elif action == "invalid":
        response.status = INVALID
        check.review_status = "invalid"
    else:
        raise ValueError("Unknown action.")
    check.reviewed_by = getattr(reviewer, "id", None)
    check.reviewed_by_name = getattr(reviewer, "name", None)
    check.reviewed_at = now
    check.remarks = (remarks or "").strip()[:2000] or None
    return [pid for pid in touched if pid]
