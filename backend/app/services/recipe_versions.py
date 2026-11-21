"""
Recipe version history (products.recipe keeps only the latest version).

save_version()   called by PUT /products/{id}/recipe after every real change. While a live
                 survey (round) tests the latest version and no consumer has tasted it yet,
                 that version is updated in place (the survey keeps its version);
                 otherwise every real change is a new version, as before.
delete_latest_version()  removes a version saved by mistake (latest, unused by any survey)
changes_between() a short, human list of what changed, e.g.
                  ["Sugar: 0.8 kg → 0.65 kg", "Added ingredient: Cinnamon", "Step 2 Bake: 25 min → 22 min"]
latest_changes()  {product_id: latest version info} for the Catalog list badges

When the organization (users.role = "client") changes a recipe in its portal,
every active admin gets a bell notification ("client_recipe_updated") with the
product, version and changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import Product, RecipeAttachment, RecipeVersion, SurveyResponse, SurveyRound, User

CLIENT_RECIPE_UPDATED = "client_recipe_updated"
MAX_CHANGES = 12


def _norm(text) -> str:
    return " ".join(str(text or "").split()).lower()


def _amount(item: dict) -> str:
    return " ".join(x for x in (str(item.get("quantity") or "").strip(), str(item.get("unit") or "").strip()) if x) or "—"


def comparable(recipe: Optional[dict]) -> dict:
    """The recipe without bookkeeping fields, for "did anything change?"."""
    if not recipe:
        return {}
    return {k: v for k, v in recipe.items() if k != "updated_at"}


def changes_between(old: Optional[dict], new: Optional[dict]) -> list[str]:
    old, new = old or {}, new or {}
    out: list[str] = []
    if not old and new:
        out.append(f"Recipe added: {len(new.get('ingredients') or [])} ingredients, {len(new.get('steps') or [])} steps")
        return out
    if old and not new:
        return ["Recipe removed"]

    if (old.get("batch_size") or "") != (new.get("batch_size") or ""):
        out.append(f"Batch size: {old.get('batch_size') or '—'} → {new.get('batch_size') or '—'}")

    before = {_norm(i.get("name")): i for i in old.get("ingredients") or [] if i.get("name")}
    after = {_norm(i.get("name")): i for i in new.get("ingredients") or [] if i.get("name")}
    for key, item in after.items():
        if key not in before:
            out.append(f"Added ingredient: {item['name']} ({_amount(item)})")
        elif _amount(before[key]) != _amount(item):
            out.append(f"{item['name']}: {_amount(before[key])} → {_amount(item)}")
        elif _norm(before[key].get("notes")) != _norm(item.get("notes")):
            out.append(f"{item['name']}: purpose changed")
    for key, item in before.items():
        if key not in after:
            out.append(f"Removed ingredient: {item['name']}")

    old_steps = [s for s in old.get("steps") or [] if s.get("title")]
    new_steps = [s for s in new.get("steps") or [] if s.get("title")]
    old_titles = [_norm(s["title"]) for s in old_steps]
    new_titles = [_norm(s["title"]) for s in new_steps]
    for i, step in enumerate(new_steps, start=1):
        key = _norm(step["title"])
        if key not in old_titles:
            out.append(f"Added step {i}: {step['title']}")
            continue
        prev = old_steps[old_titles.index(key)]
        for field, label in (("temperature", "temperature"), ("duration", "time"), ("details", "details")):
            if _norm(prev.get(field)) != _norm(step.get(field)):
                if field == "details":
                    out.append(f"Step {i} {step['title']}: details changed")
                else:
                    out.append(f"Step {i} {step['title']}: {label} {prev.get(field) or '—'} → {step.get(field) or '—'}")
    for step in old_steps:
        if _norm(step["title"]) not in new_titles:
            out.append(f"Removed step: {step['title']}")
    kept_old = [t for t in old_titles if t in new_titles]
    kept_new = [t for t in new_titles if t in old_titles]
    if kept_old != kept_new:
        out.append("Steps reordered")

    if _norm(old.get("process")) != _norm(new.get("process")):
        out.append("Process added" if not _norm(old.get("process")) else "Process removed" if not _norm(new.get("process")) else "Process changed")
    if _norm(old.get("notes")) != _norm(new.get("notes")):
        out.append("Notes changed")
    if len(out) > MAX_CHANGES:
        out = out[: MAX_CHANGES - 1] + [f"…and {len(out) - MAX_CHANGES + 1} more changes"]
    return out


def first_version(product: Product) -> int:
    """The product's first recipe version number (set when it was added; default 1)."""
    return max(1, int(getattr(product, "start_version", None) or 1))


def _next_version(db: Session, product: Product) -> int:
    current = db.query(func.max(RecipeVersion.version)).filter(RecipeVersion.product_id == product.id).scalar()
    return int(current) + 1 if current else first_version(product)


def save_version(db: Session, product: Product, old: Optional[dict], new: Optional[dict], account: dict) -> Optional[RecipeVersion]:
    """
    Store the new version (caller has already updated product.recipe). Returns None
    when nothing really changed. A recipe saved before version history existed is
    stored first as version 1 ("before history"), so the first comparison works.
    """
    if comparable(old) == comparable(new):
        return None
    if old and not db.query(RecipeVersion.id).filter(RecipeVersion.product_id == product.id).first():
        db.add(RecipeVersion(product_id=product.id, version=first_version(product), recipe=old, changed_by_name=None,
                             changed_by_role=None, changes=["Recipe as it was before version history"],
                             created_at=product.updated_at or datetime.utcnow()))
        db.flush()
    latest = (db.query(RecipeVersion).filter(RecipeVersion.product_id == product.id)
              .order_by(RecipeVersion.version.desc()).first())
    if latest is not None and version_in_unanswered_survey(db, product.id, latest.version):
        # A survey was started on this version but nobody has tasted it yet (e.g. the survey
        # was started before the recipe was complete): update the version in place, so the
        # survey keeps testing it instead of needing a new survey for "version 2".
        previous = (db.query(RecipeVersion).filter(RecipeVersion.product_id == product.id,
                                                   RecipeVersion.version < latest.version)
                    .order_by(RecipeVersion.version.desc()).first())
        latest.recipe = new
        latest.changes = changes_between(previous.recipe if previous else None, new) or [
            f"Same recipe as version {previous.version}" if previous else "Recipe cleared"]
        latest.changed_by, latest.changed_by_name, latest.changed_by_role = (
            account.get("id"), account.get("name"), account.get("role"))
        latest.created_at = datetime.utcnow()
        db.commit()
        db.refresh(latest)
        latest.edited_in_place = True  # not stored: tells the caller no new number started
        return latest
    version = RecipeVersion(
        product_id=product.id,
        version=_next_version(db, product),
        recipe=new,
        changed_by=account.get("id"),
        changed_by_name=account.get("name"),
        changed_by_role=account.get("role"),
        changes=changes_between(old, new),
        created_at=datetime.utcnow(),
    )
    db.add(version)
    product.needs_round = 1  # a new version is surveyed only once the Super Admin starts its round
    db.commit()
    db.refresh(version)
    return version


def version_surveyed(db: Session, product_id: int, version: int) -> bool:
    """True once any survey response (even an unfinished one) used this recipe version."""
    round_ids = [rid for (rid,) in db.query(SurveyRound.id).filter(SurveyRound.product_id == product_id,
                                                                   SurveyRound.recipe_version == version)]
    q = db.query(SurveyResponse.id).filter(SurveyResponse.product_id == product_id)
    cond = SurveyResponse.recipe_version == version
    if round_ids:
        cond = cond | SurveyResponse.round_id.in_(round_ids)
    return q.filter(cond).first() is not None


def version_in_unanswered_survey(db: Session, product_id: int, version: int) -> bool:
    """A LIVE survey (round) tests this version and no consumer response has used it yet.
    (After a survey ends, a recipe change is a new version for the next survey, as before.)"""
    started = db.query(SurveyRound.id).filter(SurveyRound.product_id == product_id,
                                              SurveyRound.recipe_version == version,
                                              SurveyRound.status == "live").first()
    return started is not None and not version_surveyed(db, product_id, version)


class VersionError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def delete_latest_version(db: Session, product: Product, number: int) -> Optional[RecipeVersion]:
    """
    Deletes a recipe version saved by mistake. Only the latest version, and only while no
    survey uses it. The product's recipe goes back to the previous version (or none);
    files attached to the deleted version move to the previous one. Returns the version
    that is now current (None: no recipe left).
    """
    v = db.query(RecipeVersion).filter(RecipeVersion.product_id == product.id, RecipeVersion.version == number).first()
    if v is None:
        raise VersionError(f"Version {number} doesn't exist for this product.", 404)
    latest = db.query(func.max(RecipeVersion.version)).filter(RecipeVersion.product_id == product.id).scalar()
    if number != latest:
        raise VersionError(f"Only the latest version (v{latest}) can be deleted.")
    used = (db.query(SurveyRound.number).filter(SurveyRound.product_id == product.id, SurveyRound.recipe_version == number)
            .order_by(SurveyRound.number).all())
    if used:
        names = ", ".join(f"Survey {n}" for (n,) in used)
        raise VersionError(f"Version {number} is used by {names}. Delete that survey first "
                           "(possible while it has no responses), then delete the version.")
    if version_surveyed(db, product.id, number):
        raise VersionError(f"Consumers have already tasted version {number}, so it is kept.")
    previous = (db.query(RecipeVersion).filter(RecipeVersion.product_id == product.id, RecipeVersion.version < number)
                .order_by(RecipeVersion.version.desc()).first())
    db.query(RecipeAttachment).filter(RecipeAttachment.product_id == product.id,
                                      RecipeAttachment.recipe_version == number).update(
        {RecipeAttachment.recipe_version: previous.version if previous else None}, synchronize_session=False)
    db.delete(v)
    product.recipe = previous.recipe if previous else None
    db.commit()
    return previous


def notify_admins_of_client_change(db: Session, product: Product, version: RecipeVersion) -> int:
    """Bell notification to every active admin when the organization changed a recipe."""
    from app.services.notifications import _product_info, notify

    admins = [uid for (uid,) in db.query(User.id).filter(User.role == "admin", User.is_active == 1).all()]
    info = _product_info(db, product.id)
    who = version.changed_by_name or "A client"
    changes = version.changes or []
    first = changes[0] if changes else "recipe saved"
    more = f" and {len(changes) - 1} more" if len(changes) > 1 else ""
    return notify(
        db, admins, CLIENT_RECIPE_UPDATED,
        title=f"Recipe updated by {who}",
        message=f"{who} updated the recipe of {info['product_name']} (version {version.version}): {first}{more}.",
        data={**info, "version": version.version, "client_name": who, "changes": changes,
              "changed_at": version.created_at.isoformat(timespec="seconds") + "Z"},
        dedupe_key=f"recipe:{product.id}:v{version.version}",
    )


def version_out(v: RecipeVersion, viewer_role: Optional[str] = None) -> dict:
    by_name = v.changed_by_name
    if viewer_role == "client" and v.changed_by_role == "admin":
        by_name = "Research team"  # the organization doesn't see the names of the research team's admins
    return {
        "version": v.version,
        "recipe": v.recipe,
        "changes": v.changes or [],
        "changed_by_name": by_name,
        "changed_by_role": v.changed_by_role,
        "changed_at": v.created_at.isoformat(timespec="seconds") + "Z" if v.created_at else None,
    }


def latest_changes(db: Session, product_ids=None) -> dict:
    """{product_id: {version, by_name, by_role, at}} of each product's newest recipe version."""
    newest = db.query(RecipeVersion.product_id, func.max(RecipeVersion.version).label("v"))
    if product_ids is not None:
        if not product_ids:
            return {}
        newest = newest.filter(RecipeVersion.product_id.in_(list(product_ids)))
    newest = newest.group_by(RecipeVersion.product_id).subquery()
    rows = (
        db.query(RecipeVersion)
        .join(newest, (RecipeVersion.product_id == newest.c.product_id) & (RecipeVersion.version == newest.c.v))
        .all()
    )
    return {
        r.product_id: {
            "version": r.version,
            "by_name": r.changed_by_name,
            "by_role": r.changed_by_role,
            "at": r.created_at.isoformat(timespec="seconds") + "Z" if r.created_at else None,
        }
        for r in rows
    }
