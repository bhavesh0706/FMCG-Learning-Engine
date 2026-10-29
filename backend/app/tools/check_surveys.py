"""
Find surveys whose answers or analysis are missing or incomplete.

    cd backend
    python -m app.tools.check_surveys          # report only, changes nothing
    python -m app.tools.check_surveys --fix    # also move answers from a retried duplicate
                                               # survey onto the survey that has the analysis

Checks every survey for:
  - no saved answers (and whether a duplicate survey of the same customer + product holds them)
  - marked "analyzed" but no analysis record linked
  - analysis without a transcript, summary or rating
  - analysis whose recording file is missing from disk
"""

import argparse
import json

from app.services.recordings import resolve_recording
from app.database.database import SessionLocal
from app.database.models import FeedbackRecord, Product, SurveyAnswer, SurveyResponse


def _json(v):
    if isinstance(v, str) and v.strip()[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="move answers from a retried duplicate survey")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        products = {p.id: p.name for p in db.query(Product).all()}
        surveys = db.query(SurveyResponse).order_by(SurveyResponse.id).all()
        answers = {}
        for rid, in db.query(SurveyAnswer.response_id).all():
            answers[rid] = answers.get(rid, 0) + 1
        records = {}
        for r in db.query(FeedbackRecord).filter(FeedbackRecord.response_id.isnot(None)).order_by(FeedbackRecord.id).all():
            records[r.response_id] = r

        problems, fixed = 0, 0
        print(f"{'Survey':>6}  {'Executive':18} {'Product':24} {'Answers':>7}  {'Analysis':10} Problem")
        print("-" * 110)
        for s in surveys:
            issues = []
            n_answers = answers.get(s.id, 0)
            rec = records.get(s.id)

            if n_answers == 0:
                sibling = next(
                    (
                        o for o in surveys
                        if o.id != s.id and o.respondent_id and o.respondent_id == s.respondent_id
                        and o.product_id == s.product_id and answers.get(o.id, 0) > 0
                    ),
                    None,
                )
                if sibling:
                    issues.append(f"answers are on duplicate survey {sibling.id} (retried survey)")
                    if args.fix and rec and not records.get(sibling.id):
                        db.query(SurveyAnswer).filter(SurveyAnswer.response_id == sibling.id).update(
                            {SurveyAnswer.response_id: s.id}, synchronize_session=False
                        )
                        db.commit()
                        answers[s.id], answers[sibling.id] = answers.get(sibling.id, 0), 0
                        fixed += 1
                        issues[-1] += " -> FIXED: answers moved here"
                else:
                    issues.append("no answers saved")

            if s.status == "analyzed" and not rec:
                issues.append("marked analyzed but no analysis linked")
            if rec:
                transcript = _json(rec.transcript)
                chapters = _json(rec.chapters) or []
                has_part_text = any(isinstance(c, dict) and c.get("transcript") for c in chapters)
                if not transcript and not has_part_text:
                    issues.append("analysis has no transcript (silent or unrecognised audio?)")
                if not rec.overall_summary:
                    issues.append("analysis has no summary")
                if not rec.overall_rating:
                    issues.append("analysis has no rating")
                if rec.video_path and resolve_recording(rec.video_path) is None:
                    issues.append("recording file missing on disk")

            if issues:
                problems += 1
                print(
                    f"{s.id:>6}  {str(s.executive_name or '-')[:18]:18} {products.get(s.product_id, '?')[:24]:24} "
                    f"{answers.get(s.id, 0):>7}  {('#' + str(rec.id)) if rec else '-':10} {'; '.join(issues)}"
                )
        print("-" * 110)
        print(f"{len(surveys)} surveys checked, {problems} with problems" + (f", {fixed} fixed" if args.fix else "") + ".")
        if problems and not args.fix:
            print("Run with --fix to move answers from retried duplicate surveys.")
    finally:
        db.close()


if __name__ == "__main__":
    main()