"""The bell: remove one notification, clear them all; a removed one never comes back."""
from conftest import auth, make_user

from app.services.notifications import notify


def test_delete_one_and_clear_all(client, db):
    me, other = make_user(db), make_user(db)
    for i in range(3):
        notify(db, [me.id], "product_assigned", f"N{i}", "msg", dedupe_key=f"clear-test-{me.id}-{i}")
    notify(db, [other.id], "product_assigned", "Theirs", "msg")
    db.commit()
    items = client.get("/notifications", headers=auth(me)).json()["notifications"]
    assert len(items) == 3
    first = items[0]["id"]
    # Nobody removes someone else's notification.
    theirs = client.get("/notifications", headers=auth(other)).json()["notifications"][0]["id"]
    assert client.delete(f"/notifications/{theirs}", headers=auth(me)).status_code == 404

    res = client.delete(f"/notifications/{first}", headers=auth(me))
    assert res.status_code == 200 and res.json()["unread_count"] == 2
    assert [n["id"] for n in client.get("/notifications", headers=auth(me)).json()["notifications"]].count(first) == 0
    # The same notification (same dedupe key) is not sent again after being removed.
    notify(db, [me.id], "product_assigned", "N0", "msg", dedupe_key=f"clear-test-{me.id}-0")
    db.commit()
    assert len(client.get("/notifications", headers=auth(me)).json()["notifications"]) == 2

    cleared = client.delete("/notifications", headers=auth(me)).json()
    assert cleared["cleared"] == 2 and cleared["unread_count"] == 0
    assert client.get("/notifications", headers=auth(me)).json()["notifications"] == []
    assert client.get("/notifications/unread-count", headers=auth(me)).json()["unread_count"] == 0
    assert len(client.get("/notifications", headers=auth(other)).json()["notifications"]) == 1, "others keep theirs"
