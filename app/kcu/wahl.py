"""Wahlen: eine Abstimmung, deren Ergebnis eine Rolle vergibt (z. B. Vorstand, Kassenwart).

Eine Wahl ist ein Eintrag in `polls` mit kind='wahl'. Kandidaten sind Optionen mit member_id.
Sie endet, wenn die Frist abgelaufen ist, alle aktiven Mitglieder abgestimmt haben oder jemand
sie beendet. Dann werden die Gewählten ermittelt, die Rolle (auf Wunsch) automatisch übergeben
und alle benachrichtigt.
"""
from datetime import datetime

ELECT_ROLES = {"vorstand": "Vorstand", "kassenwart": "Kassenwart", "redaktion": "Redaktion"}


def active_ids(db):
    return [r["id"] for r in db.execute("SELECT id FROM members WHERE status='aktiv'")]


def tally(db, poll_id):
    """Kandidaten mit Stimmenzahl, absteigend."""
    rows = db.execute(
        "SELECT o.id, o.label, o.member_id, (SELECT COUNT(*) FROM poll_votes v WHERE v.option_id=o.id) n "
        "FROM poll_options o WHERE o.poll_id=? ORDER BY n DESC, o.sort, o.id", (poll_id,)).fetchall()
    return [dict(r) for r in rows]


def outcome(db, p):
    """(gewählte Kandidaten, Gleichstand an der Grenze?)"""
    seats = max(1, p["seats"] or 1)
    t = [r for r in tally(db, p["id"]) if r["n"] > 0]
    if not t:
        return [], False
    winners = t[:seats]
    tie = len(t) > seats and t[seats - 1]["n"] == t[seats]["n"]
    return winners, tie


def due(db, p):
    if p["kind"] != "wahl" or p["finalized_at"]:
        return False
    if p["status"] == "geschlossen":
        return True
    if p["closes_at"] and datetime.now().strftime("%Y-%m-%dT%H:%M") >= p["closes_at"]:
        return True
    voted = {r["member_id"] for r in db.execute("SELECT member_id FROM poll_voters WHERE poll_id=?", (p["id"],))}
    act = active_ids(db)
    return bool(act) and all(m in voted for m in act)


def finalize(db, p, force=False):
    """Wahl auswerten. Rückgabe: Text des Ergebnisses oder None, wenn (noch) nichts zu tun ist."""
    if p["kind"] != "wahl" or p["finalized_at"] or not (force or due(db, p)):
        return None
    from . import notify
    role = p["role"]
    label = ELECT_ROLES.get(role, role)
    winners, tie = outcome(db, p)
    names = ", ".join(w["label"] for w in winners)
    applied = False
    if not winners:
        text = "Keine Stimmen abgegeben – niemand gewählt."
    elif tie:
        text = f"Gleichstand um den letzten Platz – bitte Stichwahl oder von Hand entscheiden. Vorne: {names}"
    else:
        text = f"Gewählt: {names}"
        if p["auto_apply"]:
            db.execute("DELETE FROM member_roles WHERE role=?", (role,))
            for w in winners:
                if w["member_id"]:
                    db.execute("INSERT OR IGNORE INTO member_roles(member_id, role) VALUES (?,?)", (w["member_id"], role))
            applied = True
    db.execute("UPDATE polls SET status='geschlossen', finalized_at=?, result=? WHERE id=?",
               (datetime.now().strftime("%Y-%m-%d %H:%M"), text + (" (Rolle übernommen)" if applied else ""), p["id"]))
    db.commit()
    body = text + (f" – die Rolle „{label}“ ist jetzt vergeben." if applied else "")
    notify.deliver_async(active_ids(db), f"🗳️ Wahlergebnis {label}", body, f"/abstimmungen/{p['id']}",
                         key=f"wahl-{p['id']}")
    return text


def check_all(db):
    n = 0
    for p in db.execute("SELECT * FROM polls WHERE kind='wahl' AND finalized_at IS NULL").fetchall():
        if finalize(db, p):
            n += 1
    return n
