"""
Recipe version history (products.recipe keeps only the latest version).

save_version()   called by PUT /products/{id}/recipe after every real change
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

from app.database.models import Product, RecipeVersion, User

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

    if _norm(old.get("notes")) != _norm(new.get("notes")):
        out.append("Notes changed")
    if len(out) > MAX_CHANGES:
        out = out[: MAX_CHANGES - 1] + [f"…and {len(out) - MAX_CHANGES + 1} more changes"]
    return out


def _next_version(db: Session, product_id: int) -> int:
    current = db.query(func.max(RecipeVersion.version)).filter(RecipeVersion.product_id == product_id).scalar()
    return int(current or 0) + 1


def save_version(db: Session, product: Product, old: Optional[dict], new: Optional[dict], account: dict) -> Optional[RecipeVersion]:
    """
    Store the new version (caller has already updated product.recipe). Returns None
    when nothing really changed. A recipe saved before version history existed is
    stored first as version 1 ("before history"), so the first comparison works.
    """
    if comparable(old) == comparable(new):
        return None
    if old and not db.query(RecipeVersion.id).filter(RecipeVersion.product_id == product.id).first():
        db.add(RecipeVersion(product_id=product.id, version=1, recipe=old, changed_by_name=None,
                             changed_by_role=None, changes=["Recipe as it was before version history"],
                             created_at=product.updated_at or datetime.utcnow()))
        db.flush()
    version = RecipeVersion(
        product_id=product.id,
        version=_next_version(db, product.id),
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