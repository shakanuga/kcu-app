"""Alle Seiten der KCU-App."""
import json
import os
import re
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)

from . import auth, logic, media
from .db import get_db, set_setting

bp = Blueprint("main", __name__)


# --- Helfer -------------------------------------------------------------------

def require(perm):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not auth.can(perm):
                abort(403, "Dafür fehlen dir die Rechte.")
            return fn(*a, **kw)
        return wrapper
    return deco


def actor():
    u = g.user
    return u.get("short") or u.get("name") or u.get("email")


def parse_amount(s):
    try:
        return round(float(str(s).replace("€", "").replace(" ", "").replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None


def parse_date(s, default=None):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return default


def active_members():
    return get_db().execute(
        "SELECT * FROM members WHERE status IN ('aktiv','ehren') ORDER BY CASE status WHEN 'aktiv' THEN 0 ELSE 1 END, sort, name"
    ).fetchall()


# --- Öffentliche Kleinigkeiten ------------------------------------------------

@bp.route("/health")
def health():
    return "ok"


@bp.route("/manifest.webmanifest")
def manifest():
    resp = jsonify({
        "id": "/",
        "name": "Kicker Club United",
        "short_name": "KCU",
        "description": "Termine, Abstimmungen, Strafen, Kasse und Kicker-Statistik des KCU",
        "lang": "de",
        "dir": "ltr",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#111113",
        "theme_color": "#111113",
        "categories": ["sports", "social"],
        "icons": [
            {"src": url_for("static", filename="img/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="img/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="img/icon-maskable-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": url_for("static", filename="img/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Termine", "url": "/termine"},
            {"name": "Strafen", "url": "/strafen"},
            {"name": "Kicker", "url": "/kicker/"},
        ],
    })
    resp.mimetype = "application/manifest+json"
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.route("/files/<name>")
def files(name):
    if "/" in name or "\\" in name or name.startswith("."):
        abort(404)
    as_dl = request.args.get("dl") == "1"
    dl_name = (request.args.get("fn") or name).replace("/", "_").replace("\\", "_")
    return send_from_directory(media.uploads_dir(), name, as_attachment=as_dl, download_name=dl_name,
                               max_age=3600)


# --- Start --------------------------------------------------------------------

@bp.route("/")
def start():
    db = get_db()
    s = g.settings
    logic.ensure_sessions(s)
    session = logic.next_session(s)
    days = (date.fromisoformat(session["date"]) - date.today()).days if session else None
    my_rsvp, summary = None, None
    if session:
        summary = logic.rsvp_summary(session["id"])
        if g.user.get("id"):
            my_rsvp = db.execute("SELECT * FROM event_rsvp WHERE event_id=? AND member_id=?",
                                 (session["id"], g.user["id"])).fetchone()
    my_account = None
    if g.user.get("id"):
        m = db.execute("SELECT * FROM members WHERE id=?", (g.user["id"],)).fetchone()
        my_account = logic.member_account(m, s)
    recent = db.execute(
        "SELECT p.*, m.short, m.name FROM penalties p JOIN members m ON m.id=p.member_id "
        "ORDER BY p.created_at DESC, p.id DESC LIMIT 6").fetchall()
    events = db.execute(
        "SELECT * FROM events WHERE kind<>'sitzung' AND cancelled=0 AND (date IS NULL OR date='' OR date >= ?) "
        "ORDER BY CASE WHEN date IS NULL OR date='' THEN 1 ELSE 0 END, date, sort LIMIT 6",
        (date.today().isoformat(),)).fetchall()
    last_protocol = db.execute("SELECT * FROM protocols ORDER BY date DESC, id DESC LIMIT 1").fetchone()
    merch_count = db.execute("SELECT COUNT(*) FROM merch").fetchone()[0]
    years = date.today().year - int(s.get("founded") or 2017)
    return render_template("start.html", session=session, days=days, my_rsvp=my_rsvp, summary=summary,
                           moved=bool(session) and not logic.is_regular_date(session["date"]),
                           my_account=my_account, recent=recent, events=events,
                           open_polls=_open_polls_for_me(),
                           pending_rsvps=[e for e in _pending_rsvps()
                                          if e["date"] <= (date.today() + timedelta(days=21)).isoformat()],
                           last_protocol=last_protocol, merch_count=merch_count, years=years,
                           birthdays=logic.birthdays(14),
                           cash=logic.cash_balance(s))


# --- Strafen ------------------------------------------------------------------

@bp.route("/strafen", methods=["GET", "POST"])
def strafen():
    db = get_db()
    if request.method == "POST":
        cat_id = request.form.get("catalog_id", type=int)
        member_ids = request.form.getlist("member_ids", type=int)
        d = parse_date(request.form.get("date"), date.today().isoformat())
        note = (request.form.get("note") or "").strip()[:300]
        cat = db.execute("SELECT * FROM catalog WHERE id=? AND active=1", (cat_id,)).fetchone()
        if not cat:
            flash("Bitte eine Strafe auswählen.", "error")
        elif not member_ids:
            flash("Bitte mindestens ein Mitglied auswählen.", "error")
        else:
            valid = {m["id"] for m in active_members()}
            n = 0
            for mid in member_ids:
                if mid in valid:
                    db.execute(
                        "INSERT INTO penalties(member_id, catalog_id, kind, title, amount, natural, date, note, created_by) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (mid, cat["id"], cat["kind"], cat["title"], cat["amount"], cat["natural"], d, note, actor()))
                    n += 1
            db.commit()
            flash(f"{n} × „{cat['title']}“ eingetragen.", "ok")
            nxt = request.form.get("next") or ""
            if nxt.startswith("/") and not nxt.startswith(("//", "/\\")):
                return redirect(nxt)
            return redirect(url_for("main.strafen"))

    member_filter = request.args.get("m", type=int)
    q = ("SELECT p.*, m.short, m.name, m.number FROM penalties p JOIN members m ON m.id=p.member_id ")
    args = []
    if member_filter:
        q += "WHERE p.member_id=? "
        args.append(member_filter)
    q += "ORDER BY p.date DESC, p.created_at DESC, p.id DESC LIMIT 100"
    rows = db.execute(q, args).fetchall()

    year = str(date.today().year)
    sinners = db.execute(
        "SELECT m.short, m.name, m.number, COUNT(*) c, COALESCE(SUM(p.amount),0) s FROM penalties p "
        "JOIN members m ON m.id=p.member_id WHERE substr(p.date,1,4)=? GROUP BY m.id ORDER BY s DESC, c DESC LIMIT 5",
        (year,)).fetchall()
    catalog = db.execute("SELECT * FROM catalog WHERE active=1 ORDER BY CASE kind WHEN 'schnell' THEN 0 ELSE 1 END, sort").fetchall()
    return render_template("strafen.html", rows=rows, catalog=catalog, members=active_members(),
                           sinners=sinners, year=year, member_filter=member_filter,
                           now=datetime.utcnow())


def _may_delete_penalty(p):
    if auth.can("kasse"):
        return True
    try:
        created = datetime.strptime(p["created_at"], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    return p["created_by"] == actor() and datetime.utcnow() - created < timedelta(minutes=30)


@bp.app_template_global()
def may_delete_penalty(p):
    return _may_delete_penalty(p)


@bp.route("/strafen/<int:pid>/loeschen", methods=["POST"])
def strafe_loeschen(pid):
    db = get_db()
    p = db.execute("SELECT * FROM penalties WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    if not _may_delete_penalty(p):
        abort(403, "Eigene Einträge kannst du 30 Minuten lang löschen, danach nur der Kassenwart.")
    db.execute("DELETE FROM penalties WHERE id=?", (pid,))
    db.commit()
    flash("Strafe gelöscht.", "ok")
    return redirect(request.referrer or url_for("main.strafen"))


# --- Kasse --------------------------------------------------------------------

@bp.route("/kasse")
def kasse():
    db = get_db()
    s = g.settings
    accounts = logic.all_accounts(s)
    total_open = sum(a["open"] for a in accounts if a["open"] > 0)
    payments = db.execute(
        "SELECT p.*, m.short, m.name FROM payments p JOIN members m ON m.id=p.member_id "
        "ORDER BY p.date DESC, p.id DESC LIMIT 30").fetchall()
    ledger = db.execute(
        "SELECT l.*, p.name AS project_name FROM ledger l LEFT JOIN projects p ON p.id=l.project_id "
        "WHERE l.project_id IS NULL OR p.status='freigegeben' ORDER BY l.date DESC, l.id DESC LIMIT 30").fetchall()
    my_projects = _visible_projects()
    return render_template("kasse.html", accounts=accounts, total_open=total_open,
                           cash=logic.cash_balance(s), payments=payments, ledger=ledger,
                           members=active_members(), projects=my_projects,
                           vertretung=_vertretung_info(),
                           may_set_vertretung=auth.has_role("kassenwart") or auth.has_role("vorstand") or auth.has_role("admin"))


@bp.route("/kasse/mitglied/<int:mid>")
def kasse_mitglied(mid):
    db = get_db()
    m = db.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
    if not m:
        abort(404)
    acc = logic.member_account(m, g.settings)
    pens = db.execute("SELECT * FROM penalties WHERE member_id=? ORDER BY date DESC, id DESC", (mid,)).fetchall()
    pays = db.execute("SELECT * FROM payments WHERE member_id=? ORDER BY date DESC, id DESC", (mid,)).fetchall()
    return render_template("konto.html", acc=acc, m=m, pens=pens, pays=pays)


MONTH_NAMES = ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september",
               "oktober", "november", "dezember"]


def parse_period(s, ref_date=None):
    """'2026-07', '07.2026', '7/2026', 'Juli 2026' oder 'Juli' (Jahr aus ref_date) -> 'YYYY-MM' oder None."""
    s = (s or "").strip().lower()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s) or None
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{1,2})[./](\d{2,4})", s)
        if m:
            mo, y = int(m.group(1)), int(m.group(2))
            y = y + 2000 if y < 100 else y
        else:
            m = re.fullmatch(r"([a-zäöü]+)\.?\s*(\d{2,4})?", s)
            if not m:
                return None
            name = m.group(1)
            hit = [i for i, n in enumerate(MONTH_NAMES, 1) if n.startswith(name[:3])]
            if not hit:
                return None
            mo = hit[0]
            y = int(m.group(2)) if m.group(2) else int((ref_date or date.today().isoformat())[:4])
            y = y + 2000 if y < 100 else y
    return f"{y:04d}-{mo:02d}" if 1 <= mo <= 12 and 2000 <= y <= 2100 else None


PAY_METHODS = {"bar": "Bar", "paypal": "PayPal", "ueberweisung": "Überweisung", "verrechnet": "Verrechnet"}


@bp.app_template_global()
def pay_method_label(m):
    return PAY_METHODS.get(m or "", "")


@bp.route("/kasse/zahlung", methods=["POST"])
@require("kasse")
def zahlung():
    db = get_db()
    mid = request.form.get("member_id", type=int)
    amount = parse_amount(request.form.get("amount"))
    d = parse_date(request.form.get("date"), date.today().isoformat())
    note = (request.form.get("note") or "").strip()[:200]
    method = request.form.get("method") if request.form.get("method") in PAY_METHODS else None
    period = parse_period(request.form.get("period"), d)
    if not mid or not amount or amount <= 0:
        flash("Bitte Mitglied und einen Betrag größer 0 angeben.", "error")
    else:
        db.execute("INSERT INTO payments(member_id, date, amount, note, method, period, created_by) VALUES (?,?,?,?,?,?,?)",
                   (mid, d, amount, note, method, period, actor()))
        db.commit()
        flash(f"Zahlung über {logic.euro(amount)} gebucht.", "ok")
    return redirect(request.referrer or url_for("main.kasse"))


@bp.route("/kasse/zahlung/<int:pid>/loeschen", methods=["POST"])
@require("kasse")
def zahlung_loeschen(pid):
    db = get_db()
    db.execute("DELETE FROM payments WHERE id=?", (pid,))
    db.commit()
    flash("Zahlung gelöscht.", "ok")
    return redirect(request.referrer or url_for("main.kasse"))


@bp.route("/kasse/buch", methods=["POST"])
@require("kasse")
def kassenbuch():
    db = get_db()
    kind = request.form.get("kind")
    amount = parse_amount(request.form.get("amount"))
    purpose = (request.form.get("purpose") or "").strip()[:200]
    d = parse_date(request.form.get("date"), date.today().isoformat())
    if kind not in ("einnahme", "ausgabe") or not amount or amount <= 0 or not purpose:
        flash("Bitte Art, Betrag und Zweck angeben.", "error")
    else:
        db.execute("INSERT INTO ledger(date, kind, amount, purpose, created_by) VALUES (?,?,?,?,?)",
                   (d, kind, amount, purpose, actor()))
        db.commit()
        flash("Eintrag im Kassenbuch gespeichert.", "ok")
    return redirect(url_for("main.kasse") + "#kassenbuch")


@bp.route("/kasse/buch/<int:lid>/loeschen", methods=["POST"])
@require("kasse")
def kassenbuch_loeschen(lid):
    db = get_db()
    db.execute("DELETE FROM ledger WHERE id=? AND project_id IS NULL", (lid,))
    db.commit()
    flash("Eintrag gelöscht.", "ok")
    return redirect(url_for("main.kasse") + "#kassenbuch")


# --- Arbeitskreise & Projekte (z. B. Mannschaftsfahrt) ------------------------
# Solange ein Projekt "geheim" ist, sieht nur das Planungsteam Ausgaben und Beträge.
# Erst nach der Freigabe durch das Team erscheinen sie im Kassenbuch und im Kassenstand.

def _team_ids(pid):
    return {r["member_id"] for r in get_db().execute(
        "SELECT member_id FROM project_members WHERE project_id=?", (pid,))}


def _in_team(pid):
    uid = g.user.get("id")
    return uid is not None and uid in _team_ids(pid)


def _visible_projects():
    """Alle Projekte mit Info, ob der Nutzer Einblick hat."""
    db = get_db()
    out = []
    for p in db.execute("SELECT * FROM projects ORDER BY status='freigegeben', created_at DESC").fetchall():
        team = _team_ids(p["id"])
        member_of = g.user.get("id") in team
        visible = member_of or p["status"] == "freigegeben"
        if not visible and not auth.can("projekt_anlegen"):
            continue  # normale Mitglieder sehen geheime Projekte gar nicht
        d = dict(p)
        d["in_team"] = member_of
        d["visible"] = visible
        d["team_size"] = len(team)
        d["totals"] = logic.project_totals(p["id"]) if visible else None
        out.append(d)
    return out


@bp.route("/projekte")
def projekte():
    return render_template("projekte.html", projects=_visible_projects())


@bp.route("/projekte/<int:pid>")
def projekt(pid):
    db = get_db()
    p = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    in_team = _in_team(pid)
    if p["status"] != "freigegeben" and not in_team:
        abort(403, "Dieses Projekt ist noch geheim – nur das Planungsteam sieht die Details.")
    entries = db.execute("SELECT * FROM ledger WHERE project_id=? ORDER BY date DESC, id DESC", (pid,)).fetchall()
    team = db.execute("SELECT m.* FROM members m JOIN project_members pm ON pm.member_id=m.id "
                      "WHERE pm.project_id=? ORDER BY m.sort, m.name", (pid,)).fetchall()
    return render_template("projekt.html", p=p, entries=entries, team=team, in_team=in_team,
                           totals=logic.project_totals(pid),
                           cash_hidden=logic.cash_balance(g.settings, include_hidden=True))


@bp.route("/projekte/neu", methods=["GET", "POST"])
@bp.route("/projekte/<int:pid>/bearbeiten", methods=["GET", "POST"])
def projekt_form(pid=None):
    db = get_db()
    p = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone() if pid else None
    if pid and not p:
        abort(404)
    if pid:
        # Geheime Projekte bearbeitet nur das Planungsteam – auch Admins schauen da nicht rein
        if not (_in_team(pid) or (p["status"] == "freigegeben" and auth.can("projekt_anlegen"))):
            abort(403, "Dieses Projekt ist noch geheim – nur das Planungsteam kann es bearbeiten.")
    elif not auth.can("projekt_anlegen"):
        abort(403, "Projekte legen Admin, Vorstand oder Kassenwart an.")
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:120]
        desc = (request.form.get("description") or "").strip()[:2000]
        budget = parse_amount(request.form.get("budget")) if request.form.get("budget") else None
        team = set(request.form.getlist("team", type=int)) & {m["id"] for m in active_members()}
        if not name:
            flash("Bitte einen Namen angeben.", "error")
        elif not team:
            flash("Bitte mindestens eine Person ins Planungsteam nehmen.", "error")
        else:
            if p:
                db.execute("UPDATE projects SET name=?, description=?, budget=? WHERE id=?", (name, desc, budget, pid))
            else:
                pid = db.execute("INSERT INTO projects(name, description, budget, created_by) VALUES (?,?,?,?)",
                                 (name, desc, budget, actor())).lastrowid
            db.execute("DELETE FROM project_members WHERE project_id=?", (pid,))
            for mid in team:
                db.execute("INSERT INTO project_members(project_id, member_id) VALUES (?,?)", (pid, mid))
            db.commit()
            flash("Projekt gespeichert.", "ok")
            if _in_team(pid):
                return redirect(url_for("main.projekt", pid=pid))
            return redirect(url_for("main.projekte"))
    return render_template("projekt_form.html", p=p, members=active_members(),
                           team=_team_ids(pid) if pid else set())


@bp.route("/projekte/<int:pid>/eintrag", methods=["POST"])
def projekt_eintrag(pid):
    db = get_db()
    if not _in_team(pid):
        abort(403, "Nur das Planungsteam kann Ausgaben eintragen.")
    kind = request.form.get("kind")
    amount = parse_amount(request.form.get("amount"))
    purpose = (request.form.get("purpose") or "").strip()[:200]
    d = parse_date(request.form.get("date"), date.today().isoformat())
    if kind not in ("einnahme", "ausgabe") or not amount or amount <= 0 or not purpose:
        flash("Bitte Art, Betrag und Zweck angeben.", "error")
    else:
        db.execute("INSERT INTO ledger(date, kind, amount, purpose, created_by, project_id) VALUES (?,?,?,?,?,?)",
                   (d, kind, amount, purpose, actor(), pid))
        db.commit()
        flash("Eintrag gespeichert.", "ok")
    return redirect(url_for("main.projekt", pid=pid))


@bp.route("/projekte/<int:pid>/eintrag/<int:lid>/loeschen", methods=["POST"])
def projekt_eintrag_loeschen(pid, lid):
    if not _in_team(pid):
        abort(403)
    db = get_db()
    db.execute("DELETE FROM ledger WHERE id=? AND project_id=?", (lid, pid))
    db.commit()
    flash("Eintrag gelöscht.", "ok")
    return redirect(url_for("main.projekt", pid=pid))


@bp.route("/projekte/<int:pid>/freigeben", methods=["POST"])
def projekt_freigeben(pid):
    if not _in_team(pid):
        abort(403, "Nur das Planungsteam kann das Projekt freigeben.")
    db = get_db()
    db.execute("UPDATE projects SET status='freigegeben', released_at=datetime('now'), released_by=? WHERE id=?",
               (actor(), pid))
    db.commit()
    flash("Projekt freigegeben – jetzt sieht der ganze Verein die Abrechnung.", "ok")
    return redirect(url_for("main.projekt", pid=pid))


@bp.route("/projekte/<int:pid>/loeschen", methods=["POST"])
def projekt_loeschen(pid):
    db = get_db()
    p = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    n = db.execute("SELECT COUNT(*) FROM ledger WHERE project_id=?", (pid,)).fetchone()[0]
    if not (_in_team(pid) or (auth.can("projekt_anlegen") and n == 0)):
        abort(403, "Projekte mit Einträgen kann nur das Team selbst löschen.")
    db.execute("DELETE FROM ledger WHERE project_id=?", (pid,))
    db.execute("DELETE FROM project_members WHERE project_id=?", (pid,))
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    db.commit()
    flash("Projekt gelöscht.", "ok")
    return redirect(url_for("main.projekte"))


# --- Galerie ------------------------------------------------------------------

@bp.route("/galerie/")
@bp.route("/galerie/<path:rel>")
def galerie(rel=""):
    if not os.path.isdir(media.photos_dir()):
        return render_template("galerie.html", missing=True, rel="", crumbs=[], listing=None)
    listing = media.list_folder(rel)
    if listing is None:
        abort(404)
    crumbs, acc = [], ""
    for part in [p for p in rel.split("/") if p]:
        acc = f"{acc}/{part}" if acc else part
        crumbs.append((part, acc))
    return render_template("galerie.html", missing=False, rel=rel, crumbs=crumbs, listing=listing)


@bp.route("/foto/<size>/<path:rel>")
def foto(size, rel):
    path = media.safe_photo_path(rel)
    if not path or not os.path.isfile(path):
        abort(404)
    ext = os.path.splitext(path)[1].lower()
    if size == "original" or ext in media.VIDEO_EXT:
        return send_file(path, as_attachment=request.args.get("dl") == "1", max_age=86400, conditional=True)
    px = {"thumb": 480, "medium": 1800}.get(size)
    if not px or ext not in media.IMAGE_EXT:
        abort(404)
    try:
        out = media.cached_resize(path, px)
    except Exception:
        abort(404)
    return send_file(out, mimetype="image/jpeg", max_age=86400 * 7)


# --- Merch-Museum -------------------------------------------------------------

MERCH_CATEGORIES = ["Trikot", "Shirt", "Hoodie / Jacke", "Weste", "Mütze / Schal", "Pokal", "Sticker / Druck",
                    "Becher / Glas", "Sonstiges"]
MERCH_KINDS = {
    "sammel": ("👕", "Sammelbestellung", "Für die Truppe bestellt – jeder hat seins"),
    "einzel": ("🏆", "Einzelstück", "Gibt's nur einmal, z. B. Pokal, Weste oder Banner"),
}


def _merch_with_cover(rows):
    db = get_db()
    out = []
    for r in rows:
        ph = db.execute("SELECT file FROM merch_photos WHERE merch_id=? ORDER BY sort, id LIMIT 1", (r["id"],)).fetchone()
        d = dict(r)
        d["cover"] = ph["file"] if ph else None
        d["n_owned"] = db.execute("SELECT COALESCE(SUM(qty),0) FROM merch_owners WHERE merch_id=?", (r["id"],)).fetchone()[0]
        d["holder"] = db.execute("SELECT * FROM members WHERE id=?", (r["holder_id"],)).fetchone() if r["holder_id"] else None
        out.append(d)
    return out


def _merch_members():
    return get_db().execute("SELECT * FROM members WHERE status IN ('aktiv','ehren','ehemalig') ORDER BY "
                            "CASE status WHEN 'aktiv' THEN 0 WHEN 'ehren' THEN 1 ELSE 2 END, sort, name").fetchall()


@bp.route("/museum")
def museum():
    db = get_db()
    cat = request.args.get("k")
    q, args = "SELECT * FROM merch ", []
    if cat:
        q += "WHERE category=? "
        args.append(cat)
    q += "ORDER BY year DESC NULLS LAST, name"
    items = _merch_with_cover(db.execute(q, args).fetchall())
    by_year = {}
    for it in items:
        by_year.setdefault(it["year"] or "Ohne Jahr", []).append(it)
    used = [r["category"] for r in db.execute("SELECT DISTINCT category FROM merch WHERE category<>'' ORDER BY category")]
    return render_template("museum.html", by_year=by_year, categories=used, current=cat, total=len(items))


@bp.route("/museum/<int:iid>")
def museum_item(iid):
    db = get_db()
    it = db.execute("SELECT * FROM merch WHERE id=?", (iid,)).fetchone()
    if not it:
        abort(404)
    photos = db.execute("SELECT * FROM merch_photos WHERE merch_id=? ORDER BY sort, id", (iid,)).fetchall()
    owners = db.execute("SELECT m.*, o.qty FROM merch_owners o JOIN members m ON m.id=o.member_id "
                        "WHERE o.merch_id=? ORDER BY m.sort, m.name", (iid,)).fetchall()
    holder = db.execute("SELECT * FROM members WHERE id=?", (it["holder_id"],)).fetchone() if it["holder_id"] else None
    return render_template("museum_item.html", it=it, photos=photos, owners=owners, holder=holder,
                           kinds=MERCH_KINDS, n_owned=sum(o["qty"] for o in owners))


def _save_merch_photos(iid):
    db = get_db()
    n = 0
    for f in request.files.getlist("photos"):
        if f and f.filename:
            try:
                name = media.save_image_upload(f)
            except Exception:
                flash(f"„{f.filename}“ konnte nicht als Bild gelesen werden.", "error")
                continue
            db.execute("INSERT INTO merch_photos(merch_id, file, sort) VALUES (?,?,?)", (iid, name, 100 + n))
            n += 1
    return n


@bp.route("/museum/neu", methods=["GET", "POST"])
@bp.route("/museum/<int:iid>/bearbeiten", methods=["GET", "POST"])
@require("redaktion")
def museum_form(iid=None):
    db = get_db()
    it = db.execute("SELECT * FROM merch WHERE id=?", (iid,)).fetchone() if iid else None
    if iid and not it:
        abort(404)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:120]
        year = request.form.get("year", type=int)
        category = (request.form.get("category") or "").strip()[:60]
        desc = (request.form.get("description") or "").strip()[:4000]
        kind = request.form.get("kind") if request.form.get("kind") in MERCH_KINDS else "sammel"
        occasion = (request.form.get("occasion") or "").strip()[:120]
        try:
            price = round(float((request.form.get("price") or "").replace(",", ".")), 2) if request.form.get("price") else None
        except ValueError:
            price = None
        valid_ids = {m["id"] for m in _merch_members()}
        holder_id = request.form.get("holder_id", type=int) if kind == "einzel" else None
        holder_id = holder_id if holder_id in valid_ids else None
        owner_ids = [i for i in request.form.getlist("owner_ids", type=int) if i in valid_ids] if kind == "sammel" else []
        if not name:
            flash("Bitte einen Namen angeben.", "error")
        else:
            if it:
                db.execute("UPDATE merch SET name=?, year=?, category=?, description=?, kind=?, occasion=?, price=?, "
                           "holder_id=? WHERE id=?", (name, year, category, desc, kind, occasion, price, holder_id, iid))
            else:
                cur = db.execute("INSERT INTO merch(name, year, category, description, kind, occasion, price, holder_id, "
                                 "created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (name, year, category, desc, kind, occasion, price, holder_id, actor()))
                iid = cur.lastrowid
            old_qty = {r["member_id"]: r["qty"] for r in db.execute("SELECT * FROM merch_owners WHERE merch_id=?", (iid,))}
            db.execute("DELETE FROM merch_owners WHERE merch_id=?", (iid,))
            for mid in owner_ids:
                qty = max(1, min(20, request.form.get(f"qty_{mid}", type=int) or old_qty.get(mid, 1)))
                db.execute("INSERT INTO merch_owners(merch_id, member_id, qty) VALUES (?,?,?)", (iid, mid, qty))
            _save_merch_photos(iid)
            db.commit()
            flash("Gespeichert.", "ok")
            return redirect(url_for("main.museum_item", iid=iid))
    photos = db.execute("SELECT * FROM merch_photos WHERE merch_id=? ORDER BY sort, id", (iid,)).fetchall() if iid else []
    owned = {r["member_id"]: r["qty"] for r in db.execute("SELECT * FROM merch_owners WHERE merch_id=?", (iid,))} if iid else {}
    return render_template("museum_form.html", it=it, photos=photos, categories=MERCH_CATEGORIES,
                           kinds=MERCH_KINDS, members=_merch_members(), owned=owned)


@bp.route("/museum/<int:iid>/loeschen", methods=["POST"])
@require("redaktion")
def museum_loeschen(iid):
    db = get_db()
    for ph in db.execute("SELECT file FROM merch_photos WHERE merch_id=?", (iid,)).fetchall():
        media.delete_upload(ph["file"])
    db.execute("DELETE FROM merch_photos WHERE merch_id=?", (iid,))
    db.execute("DELETE FROM merch WHERE id=?", (iid,))
    db.commit()
    flash("Stück aus dem Museum entfernt.", "ok")
    return redirect(url_for("main.museum"))


@bp.route("/museum/foto/<int:pid>/loeschen", methods=["POST"])
@require("redaktion")
def museum_foto_loeschen(pid):
    db = get_db()
    ph = db.execute("SELECT * FROM merch_photos WHERE id=?", (pid,)).fetchone()
    if not ph:
        abort(404)
    media.delete_upload(ph["file"])
    db.execute("DELETE FROM merch_photos WHERE id=?", (pid,))
    db.commit()
    return redirect(url_for("main.museum_form", iid=ph["merch_id"]))


# --- Protokolle ---------------------------------------------------------------

@bp.route("/protokolle")
def protokolle():
    rows = get_db().execute("SELECT * FROM protocols ORDER BY date DESC, id DESC").fetchall()
    return render_template("protokolle.html", rows=rows)


@bp.route("/protokolle/<int:pid>")
def protokoll(pid):
    p = get_db().execute("SELECT * FROM protocols WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    return render_template("protokoll.html", p=p)


@bp.route("/protokolle/neu", methods=["GET", "POST"])
@bp.route("/protokolle/<int:pid>/bearbeiten", methods=["GET", "POST"])
def protokoll_form(pid=None):
    # Neue Protokolle darf jedes Mitglied hochladen, Bearbeiten bleibt bei der Redaktion.
    if pid and not auth.can("redaktion"):
        abort(403, "Dafür fehlen dir die Rechte.")
    db = get_db()
    p = db.execute("SELECT * FROM protocols WHERE id=?", (pid,)).fetchone() if pid else None
    if pid and not p:
        abort(404)
    if request.method == "POST":
        d = parse_date(request.form.get("date"), date.today().isoformat())
        title = (request.form.get("title") or "").strip()[:150] or f"Sitzung vom {logic.date_de(d)}"
        body = (request.form.get("body") or "").strip()[:50000]
        file, file_name = (p["file"], p["file_name"]) if p else (None, None)
        up = request.files.get("file")
        if up and up.filename:
            try:
                new = media.save_pdf_upload(up)
                if file:
                    media.delete_upload(file)
                file, file_name = new, os.path.basename(up.filename)[:120]
            except ValueError as e:
                flash(str(e), "error")
                return redirect(request.url)
        if request.form.get("remove_file") and file:
            media.delete_upload(file)
            file, file_name = None, None
        if p:
            db.execute("UPDATE protocols SET date=?, title=?, body=?, file=?, file_name=?, updated_at=datetime('now') WHERE id=?",
                       (d, title, body, file, file_name, pid))
        else:
            cur = db.execute("INSERT INTO protocols(date, title, body, file, file_name, created_by) VALUES (?,?,?,?,?,?)",
                             (d, title, body, file, file_name, actor()))
            pid = cur.lastrowid
        db.commit()
        flash("Protokoll gespeichert.", "ok")
        return redirect(url_for("main.protokoll", pid=pid))
    return render_template("protokoll_form.html", p=p)


@bp.route("/protokolle/<int:pid>/loeschen", methods=["POST"])
@require("redaktion")
def protokoll_loeschen(pid):
    db = get_db()
    p = db.execute("SELECT * FROM protocols WHERE id=?", (pid,)).fetchone()
    if p and p["file"]:
        media.delete_upload(p["file"])
    db.execute("DELETE FROM protocols WHERE id=?", (pid,))
    db.commit()
    flash("Protokoll gelöscht.", "ok")
    return redirect(url_for("main.protokolle"))


# --- Satzung ------------------------------------------------------------------

@bp.route("/satzung")
def satzung():
    db = get_db()
    strafen = db.execute("SELECT * FROM catalog WHERE kind='strafe' AND active=1 ORDER BY sort").fetchall()
    schnell = db.execute("SELECT * FROM catalog WHERE kind='schnell' AND active=1 ORDER BY sort").fetchall()
    return render_template("satzung.html", strafen=strafen, schnell=schnell)


@bp.route("/satzung/hochladen", methods=["POST"])
@require("redaktion")
def satzung_upload():
    up = request.files.get("file")
    if not up or not up.filename:
        flash("Bitte eine PDF-Datei auswählen.", "error")
        return redirect(url_for("main.satzung"))
    try:
        name = media.save_pdf_upload(up)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("main.satzung"))
    old = g.settings.get("satzung_file")
    set_setting("satzung_file", name)
    label = (request.form.get("label") or "").strip()[:100]
    if label:
        set_setting("satzung_label", label)
    if old:
        media.delete_upload(old)
    flash("Neue Satzung hochgeladen.", "ok")
    return redirect(url_for("main.satzung"))


# --- Mitglieder ---------------------------------------------------------------

@bp.route("/mitglieder")
def mitglieder():
    db = get_db()
    rows = db.execute("SELECT * FROM members ORDER BY sort, name").fetchall()
    groups = {"aktiv": [], "ehren": [], "ehemalig": []}
    for r in rows:
        groups.setdefault(r["status"], []).append(r)
    events = db.execute("SELECT * FROM events WHERE kind<>'sitzung' AND cancelled=0 ORDER BY CASE WHEN date IS NULL OR date='' THEN 1 ELSE 0 END, date, sort").fetchall()
    return render_template("mitglieder.html", groups=groups, events=events, rmap=auth.roles_map(),
                           birthdays=logic.birthdays(60))


# --- Admin --------------------------------------------------------------------

@bp.route("/admin")
@require("admin")
def admin():
    db = get_db()
    members = db.execute("SELECT * FROM members ORDER BY CASE status WHEN 'aktiv' THEN 0 WHEN 'ehren' THEN 1 ELSE 2 END, sort, name").fetchall()
    emails = [m["email"] for m in members if m["email"] and m["status"] != "ehemalig"]
    return render_template("admin/index.html", members=members, emails=emails, rmap=auth.roles_map(),
                           cf_mode=auth.cf_mode(), admin_emails=sorted(auth.admin_emails()))


@bp.route("/admin/mitglied/neu", methods=["GET", "POST"])
@bp.route("/admin/mitglied/<int:mid>", methods=["GET", "POST"])
@require("admin")
def admin_member(mid=None):
    db = get_db()
    m = db.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone() if mid else None
    if mid and not m:
        abort(404)
    if request.method == "POST":
        if request.form.get("delete") and m:
            used = db.execute("SELECT (SELECT COUNT(*) FROM penalties WHERE member_id=?) + (SELECT COUNT(*) FROM payments WHERE member_id=?)",
                              (mid, mid)).fetchone()[0]
            if used:
                flash("Mitglied hat Buchungen – statt Löschen bitte Status „Ehemalig“ setzen.", "error")
                return redirect(request.url)
            db.execute("DELETE FROM members WHERE id=?", (mid,))
            db.commit()
            flash("Mitglied gelöscht.", "ok")
            return redirect(url_for("main.admin"))
        f = request.form
        name = (f.get("name") or "").strip()[:100]
        email = (f.get("email") or "").strip().lower()[:200] or None
        roles = [r for r in f.getlist("roles") if r in auth.ROLES]
        role = roles[0] if roles else "mitglied"   # Altfeld, nur zur Info
        status = f.get("status") if f.get("status") in ("aktiv", "ehren", "ehemalig") else "aktiv"
        joined = (f.get("joined") or "").strip()[:7] or None
        left_at = (f.get("left_at") or "").strip()[:7] or None
        opening = parse_amount(f.get("opening") or "0") or 0.0
        birthday = parse_date(f.get("birthday"))
        vals = (name, (f.get("short") or "").strip()[:40], (f.get("number") or "").strip()[:4], email, role, status,
                (f.get("aufgabe") or "").strip()[:150], joined, left_at, opening, f.get("sort", type=int) or 100)
        if not name:
            flash("Name fehlt.", "error")
        else:
            try:
                if m:
                    db.execute("UPDATE members SET name=?, short=?, number=?, email=?, role=?, status=?, aufgabe=?, joined=?, "
                               "left_at=?, opening=?, sort=?, birthday=? WHERE id=?", vals + (birthday, mid))
                else:
                    mid = db.execute("INSERT INTO members(name, short, number, email, role, status, aufgabe, joined, left_at, opening, sort, birthday) "
                                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", vals + (birthday,)).lastrowid
                auth.set_roles(mid, roles)
                db.commit()
                flash("Gespeichert.", "ok")
                return redirect(url_for("main.admin"))
            except Exception:
                flash("Diese E-Mail-Adresse ist schon einem anderen Mitglied zugeordnet.", "error")
    return render_template("admin/member.html", m=m, roles=auth.ROLES, my_roles=auth.roles_of(mid) if mid else set())


@bp.route("/rollen", methods=["GET", "POST"])
@require("rollen")
def rollen():
    """Übersicht: wer hat welche Rolle – Admin und Vorstand können sie hier direkt ändern."""
    from . import wahl
    db = get_db()
    members = db.execute("SELECT * FROM members WHERE status IN ('aktiv','ehren') ORDER BY "
                         "CASE status WHEN 'aktiv' THEN 0 ELSE 1 END, sort, name").fetchall()
    editable = auth.ROLES if auth.can("admin") else [r for r in auth.ROLES if r != "admin"]
    if request.method == "POST":
        ids = {m["id"] for m in members}
        changed = []
        for m in members:
            before = auth.roles_of(m["id"])
            after = {r for r in editable if request.form.get(f"r_{m['id']}_{r}")} | (before - set(editable))
            if after != before:
                auth.set_roles(m["id"], after)
                changed.append(m["short"] or m["name"])
            aufgabe = (request.form.get(f"a_{m['id']}") or "").strip()[:150]
            if m["id"] in ids and aufgabe != (m["aufgabe"] or ""):
                db.execute("UPDATE members SET aufgabe=? WHERE id=?", (aufgabe, m["id"]))
        db.commit()
        flash(f"Rollen gespeichert ({', '.join(changed)})." if changed else "Gespeichert.", "ok")
        return redirect(url_for("main.rollen"))
    elections = db.execute("SELECT * FROM polls WHERE kind='wahl' ORDER BY created_at DESC LIMIT 10").fetchall()
    return render_template("rollen.html", members=members, rmap=auth.roles_map(), roles=auth.ROLES, editable=editable,
                           elect_roles=wahl.ELECT_ROLES, elections=elections,
                           env_admins={e.lower() for e in auth.admin_emails()}, since=auth.role_since())


@bp.route("/rollen/wahl", methods=["GET", "POST"])
@require("rollen")
def wahl_neu():
    from . import notify, wahl
    db = get_db()
    role = request.values.get("rolle") if request.values.get("rolle") in wahl.ELECT_ROLES else "vorstand"
    members = db.execute("SELECT * FROM members WHERE status='aktiv' ORDER BY sort, name").fetchall()
    holders = [m["id"] for m in members if role in auth.roles_of(m["id"])]
    if request.method == "POST":
        f = request.form
        valid = {m["id"]: m for m in members}
        replaces = [i for i in f.getlist("ersetzt", type=int) if i in holders]
        staying = [i for i in holders if i not in replaces]
        cands = [valid[i] for i in f.getlist("kandidaten", type=int) if i in valid and i not in staying]
        seats = max(1, min(10, f.get("seats", type=int) or len(replaces) or 1))
        closes = (f.get("closes_at") or "").strip()[:16] or None
        if len(cands) < 1:
            flash("Bitte mindestens einen Kandidaten auswählen.", "error")
        else:
            label = wahl.ELECT_ROLES[role]
            title = (f.get("title") or "").strip()[:150] or f"Wahl: {label}"
            stay_names = ", ".join(valid[i]["short"] or valid[i]["name"] for i in staying)
            desc = (f.get("description") or "").strip()[:1000] or (
                f"Gewählt werden {seats} Person{'en' if seats > 1 else ''} als {label}. "
                f"Du kannst bis zu {seats} Stimme{'n' if seats > 1 else ''} vergeben."
                + (f" {stay_names} bleibt im Amt." if len(staying) == 1 else
                   f" {stay_names} bleiben im Amt." if staying else ""))
            pid = db.execute(
                "INSERT INTO polls(title, description, kind, audience, anonymous, show_results, closes_at, created_by_id, "
                "created_by, role, seats, auto_apply, replaces) VALUES (?,?, 'wahl', 'alle', ?, 'nach_ende', ?,?,?,?,?,?,?)",
                (title, desc, 1 if f.get("anonymous") else 0, closes, g.user.get("id"), actor(), role, seats,
                 1 if f.get("auto_apply") else 0, json.dumps(replaces))).lastrowid
            for i, m in enumerate(cands):
                db.execute("INSERT INTO poll_options(poll_id, label, sort, member_id) VALUES (?,?,?,?)",
                           (pid, m["short"] or m["name"], i, m["id"]))
            db.commit()
            notify.on_new_poll(pid, title, "alle", g.user.get("id"), actor())
            flash("Wahl gestartet – alle wurden benachrichtigt.", "ok")
            return redirect(url_for("main.abstimmung", pid=pid))
    since = auth.role_since()
    holder_rows = sorted((m for m in members if m["id"] in holders), key=lambda m: since.get((m["id"], role)) or "")
    return render_template("wahl_form.html", role=role, elect_roles=wahl.ELECT_ROLES, members=members,
                           holders=holders, holder_rows=holder_rows, since=since, seats=max(1, len(holders)))


@bp.route("/admin/katalog", methods=["GET", "POST"])
@require("admin")
def admin_catalog():
    db = get_db()
    if request.method == "POST":
        f = request.form
        cid = f.get("id", type=int)
        if f.get("delete") and cid:
            used = db.execute("SELECT COUNT(*) FROM penalties WHERE catalog_id=?", (cid,)).fetchone()[0]
            if used:
                db.execute("UPDATE catalog SET active=0 WHERE id=?", (cid,))
                flash("Strafe wurde schon vergeben – sie ist jetzt ausgeblendet statt gelöscht.", "ok")
            else:
                db.execute("DELETE FROM catalog WHERE id=?", (cid,))
                flash("Gelöscht.", "ok")
            db.commit()
            return redirect(url_for("main.admin_catalog"))
        title = (f.get("title") or "").strip()[:150]
        kind = f.get("kind") if f.get("kind") in ("strafe", "schnell") else "strafe"
        amount = parse_amount(f.get("amount") or "0") or 0.0
        vals = (kind, (f.get("code") or "").strip()[:6], title, (f.get("description") or "").strip()[:300], amount,
                (f.get("natural") or "").strip()[:80] or None, 1 if f.get("active") else 0, f.get("sort", type=int) or 100)
        if not title:
            flash("Titel fehlt.", "error")
        else:
            if cid:
                db.execute("UPDATE catalog SET kind=?, code=?, title=?, description=?, amount=?, natural=?, active=?, sort=? WHERE id=?",
                           vals + (cid,))
            else:
                db.execute("INSERT INTO catalog(kind, code, title, description, amount, natural, active, sort) VALUES (?,?,?,?,?,?,?,?)", vals)
            db.commit()
            flash("Strafenkatalog gespeichert.", "ok")
        return redirect(url_for("main.admin_catalog"))
    rows = db.execute("SELECT * FROM catalog ORDER BY kind, sort").fetchall()
    return render_template("admin/catalog.html", rows=rows)


@bp.route("/admin/termine")
@require("admin")
def admin_events():
    return redirect(url_for("main.termine"))


SETTING_FIELDS = [
    ("app_url", "Adresse der App (für Links in Mails), z. B. https://kcu.example.org – leer lassen = automatisch"),
    ("kickertool_url", "Link zum Kickertool"), ("kicker_ziel", "Kicker: Tore bis zum Sieg (Standard)"),
    ("kicker_timer", "Kicker: Spielzeit-Timer in Minuten (Standard, z. B. 2)"),
    ("notify_mode", "Benachrichtigungen verschicken an: aus, admins (Testbetrieb) oder alle"),
    ("notify_tage_vorher", "Erinnerung: Tage vor einem Termin"), ("notify_uhrzeit", "Erinnerungen ab Uhrzeit (Stunde, z. B. 10)"),
    ("smtp_host", "E-Mail: SMTP-Server"), ("smtp_port", "E-Mail: Port (587 oder 465)"),
    ("smtp_security", "E-Mail: Verschlüsselung (starttls, ssl oder none)"),
    ("smtp_user", "E-Mail: Benutzername"), ("smtp_pass", "E-Mail: Passwort", "password"),
    ("smtp_from", "E-Mail: Absender, z. B. KCU <kcu@example.org>"),
    ("club_name", "Vereinsname"), ("club_short", "Kürzel"), ("founded", "Gründungsjahr"), ("tagline", "Motto"),
    ("beitrag", "Monatsbeitrag (€)"), ("kasse_start", "Kasse rechnet ab (JJJJ-MM)"),
    ("kasse_startbestand", "Kassenbestand zum Start (€)"), ("session_time", "Sitzungsbeginn"),
    ("rsvp_tage_vorher", "Rückmeldefrist: Tage vor einer Sitzung"),
    ("rsvp_sperre", "Nach Ablauf der Frist Zu-/Absagen sperren (1 = ja, 0 = nein)"),
    ("ki_url", "Link zur Satzungs-KI"), ("instagram_url", "Instagram"), ("stammbier", "Stammbier"),
    ("impressum", "Impressum (Name, Anschrift, Kontakt – leer = kein Impressum-Link)", "textarea"),
]


@bp.route("/admin/einstellungen", methods=["GET", "POST"])
@require("admin")
def admin_settings():
    if request.method == "POST":
        for field in SETTING_FIELDS:
            key = field[0]
            if key in request.form:
                val = (request.form.get(key) or "").strip()[:2000 if len(field) > 2 and field[2] == "textarea" else 300]
                if len(field) > 2 and field[2] == "password" and not val:
                    continue   # leeres Passwortfeld = unverändert lassen
                set_setting(key, val)
        flash("Einstellungen gespeichert.", "ok")
        return redirect(url_for("main.admin_settings"))
    from . import notify
    import os as _os
    return render_template("admin/settings.html", fields=SETTING_FIELDS, smtp_ok=notify.smtp_config(g.settings)["ok"],
                           pass_from_env=bool(_os.environ.get("KCU_SMTP_PASS")),
                           my_email=(get_db().execute("SELECT email FROM members WHERE id=?", (g.user.get("id"),)).fetchone() or {"email": ""})["email"] if g.user.get("id") else g.user.get("email"))


# =============================================================================
# Termine mit Zu-/Absage (Ersatz für SpielerPlus)
# =============================================================================

RSVP_LABEL = {"ja": "Zusage", "nein": "Absage", "vielleicht": "Vielleicht"}


@bp.app_template_global()
def rsvp_label(s):
    return RSVP_LABEL.get(s, s)


def _pending_rsvps():
    """Termine, für die das eingeloggte Mitglied noch keine Rückmeldung gegeben hat."""
    uid = g.user.get("id")
    if not uid:
        return []
    return get_db().execute(
        "SELECT * FROM events e WHERE e.rsvp=1 AND e.cancelled=0 AND e.date >= ? AND NOT EXISTS "
        "(SELECT 1 FROM event_rsvp r WHERE r.event_id=e.id AND r.member_id=?) ORDER BY e.date LIMIT 5",
        (date.today().isoformat(), uid)).fetchall()


@bp.route("/termine")
def termine():
    db = get_db()
    logic.ensure_sessions(g.settings)
    today = date.today().isoformat()
    upcoming = db.execute(
        "SELECT * FROM events WHERE date IS NOT NULL AND date<>'' AND date >= ? ORDER BY date, time LIMIT 30",
        (today,)).fetchall()
    undated = db.execute("SELECT * FROM events WHERE (date IS NULL OR date='') AND cancelled=0 ORDER BY sort").fetchall()
    past = db.execute("SELECT * FROM events WHERE date IS NOT NULL AND date<>'' AND date < ? ORDER BY date DESC LIMIT 10",
                      (today,)).fetchall()
    mine = {}
    if g.user.get("id"):
        for r in db.execute("SELECT * FROM event_rsvp WHERE member_id=?", (g.user["id"],)):
            mine[r["event_id"]] = r
    counts = {}
    for r in db.execute("SELECT event_id, status, COUNT(*) c FROM event_rsvp GROUP BY event_id, status"):
        counts.setdefault(r["event_id"], {})[r["status"]] = r["c"]
    return render_template("termine.html", upcoming=upcoming, undated=undated, past=past, mine=mine, counts=counts)


@bp.route("/termine/<int:eid>")
def termin(eid):
    db = get_db()
    e = db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    if not e:
        abort(404)
    mine = None
    if g.user.get("id"):
        mine = db.execute("SELECT * FROM event_rsvp WHERE event_id=? AND member_id=?", (eid, g.user["id"])).fetchone()
    summary = logic.rsvp_summary(eid) if e["rsvp"] else None
    # Kassenwart abgesagt? -> Vertretung vorschlagen
    kw_absent = []
    if summary:
        kw_ids = {r["member_id"] for r in db.execute("SELECT member_id FROM member_roles WHERE role='kassenwart'")}
        kw_absent = [r for r in summary["groups"]["nein"] if r["member_id"] in kw_ids]
    vorstand = db.execute("SELECT m.* FROM members m JOIN member_roles r ON r.member_id=m.id "
                          "WHERE r.role='vorstand' AND m.status='aktiv' ORDER BY m.sort").fetchall()
    deadline_passed = bool(e["rsvp_until"]) and date.today().isoformat() > e["rsvp_until"]
    polls = db.execute("SELECT * FROM polls WHERE event_id=? ORDER BY created_at DESC", (eid,)).fetchall()
    return render_template("termin.html", e=e, mine=mine, summary=summary, kw_absent=kw_absent,
                           vorstand=vorstand, members=active_members(), deadline_passed=deadline_passed,
                           polls=[p for p in polls if _poll_visible(p)],
                           vertretung=_vertretung_info())


@bp.route("/termine/<int:eid>/antwort", methods=["POST"])
def termin_antwort(eid):
    db = get_db()
    e = db.execute("SELECT * FROM events WHERE id=? AND rsvp=1", (eid,)).fetchone()
    if not e or not g.user.get("id"):
        abort(404)
    if e["cancelled"]:
        abort(400, "Der Termin ist abgesagt.")
    status = request.form.get("status")
    reason = (request.form.get("reason") or "").strip()[:300]
    guests = (request.form.get("guests") or "").strip()[:200]
    if status not in RSVP_LABEL:
        abort(400)
    if (g.settings.get("rsvp_sperre") == "1" and e["rsvp_until"] and date.today().isoformat() > e["rsvp_until"]
            and not auth.can("termine")):
        flash(f"Die Rückmeldefrist ist am {logic.date_de(e['rsvp_until'])} abgelaufen – Änderungen nur noch über den Vorstand.", "error")
        return redirect(request.referrer or url_for("main.termin", eid=eid))
    if status == "nein" and not reason:
        flash("Bei einer Absage bitte einen Grund angeben – so will es die Satzung.", "error")
        return redirect(request.referrer or url_for("main.termin", eid=eid))
    db.execute("INSERT INTO event_rsvp(event_id, member_id, status, reason, guests, updated_at) VALUES (?,?,?,?,?,datetime('now')) "
               "ON CONFLICT(event_id, member_id) DO UPDATE SET status=excluded.status, reason=excluded.reason, "
               "guests=excluded.guests, updated_at=excluded.updated_at",
               (eid, g.user["id"], status, reason, guests))
    db.commit()
    flash(f"{RSVP_LABEL[status]} gespeichert.", "ok")
    return redirect(request.referrer or url_for("main.termin", eid=eid))


@bp.route("/termine/neu", methods=["GET", "POST"])
@bp.route("/termine/<int:eid>/bearbeiten", methods=["GET", "POST"])
@require("termine")
def termin_form(eid=None):
    db = get_db()
    e = db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone() if eid else None
    if eid and not e:
        abort(404)
    if request.method == "POST":
        f = request.form
        if f.get("delete") and e:
            db.execute("DELETE FROM events WHERE id=?", (eid,))
            db.commit()
            flash("Termin gelöscht.", "ok")
            return redirect(url_for("main.termine"))
        title = (f.get("title") or "").strip()[:100]
        d = parse_date(f.get("date"))
        vals = (title, d, (f.get("time") or "").strip()[:5] or None, (f.get("location") or "").strip()[:120] or None,
                (f.get("label") or "").strip()[:60] or None, (f.get("note") or "").strip()[:500] or None,
                1 if f.get("rsvp") else 0, parse_date(f.get("rsvp_until")), 1 if f.get("cancelled") else 0,
                f.get("kind") if f.get("kind") in ("sitzung", "event") else "event")
        if not title:
            flash("Titel fehlt.", "error")
        else:
            if e:
                db.execute("UPDATE events SET title=?, date=?, time=?, location=?, label=?, note=?, rsvp=?, rsvp_until=?, "
                           "cancelled=?, kind=? WHERE id=?", vals + (eid,))
            else:
                eid = db.execute("INSERT INTO events(title, date, time, location, label, note, rsvp, rsvp_until, cancelled, kind) "
                                 "VALUES (?,?,?,?,?,?,?,?,?,?)", vals).lastrowid
            db.commit()
            from . import notify
            notify.on_event_changed(db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone(), e)
            flash("Termin gespeichert.", "ok")
            return redirect(url_for("main.termin", eid=eid))
    return render_template("termin_form.html", e=e)


@bp.route("/termine/<int:eid>/strafe-keine-antwort", methods=["POST"])
@require("kasse")
def termin_strafe_offen(eid):
    """Strafe 4 (nicht abgestimmt) für alle aktiven Mitglieder ohne Rückmeldung."""
    db = get_db()
    e = db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    if not e:
        abort(404)
    cat = db.execute("SELECT * FROM catalog WHERE kind='strafe' AND code='4' AND active=1").fetchone()
    if not cat:
        flash("Strafe Nr. 4 („Nicht abgestimmt“) fehlt im Strafenkatalog.", "error")
        return redirect(url_for("main.termin", eid=eid))
    ids = set(request.form.getlist("member_ids", type=int))
    open_ids = {m["id"] for m in logic.rsvp_summary(eid)["open"]}
    n = 0
    for mid in ids & open_ids:
        already = db.execute("SELECT 1 FROM penalties WHERE member_id=? AND catalog_id=? AND note=?",
                             (mid, cat["id"], f"Termin #{eid}")).fetchone()
        if already:
            continue
        db.execute("INSERT INTO penalties(member_id, catalog_id, kind, title, amount, natural, date, note, created_by) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (mid, cat["id"], cat["kind"], cat["title"], cat["amount"], cat["natural"], e["date"] or date.today().isoformat(), f"Termin #{eid}", actor()))
        n += 1
    db.commit()
    flash(f"{n} × „{cat['title']}“ eingetragen.", "ok")
    return redirect(url_for("main.termin", eid=eid))


# --- Kassenwart-Vertretung ----------------------------------------------------

def _vertretung_info():
    s = g.settings
    vid, bis = s.get("kasse_vertretung_id"), s.get("kasse_vertretung_bis")
    if not vid or not bis or date.today().isoformat() > bis:
        return None
    m = get_db().execute("SELECT * FROM members WHERE id=?", (vid,)).fetchone()
    return {"member": m, "bis": bis} if m else None


@bp.route("/kasse/vertretung", methods=["POST"])
def kasse_vertretung():
    if not (auth.has_role("kassenwart") or auth.has_role("vorstand") or auth.has_role("admin")):
        abort(403, "Die Vertretung bestimmen Kassenwart oder Vorstand.")
    if request.form.get("clear"):
        set_setting("kasse_vertretung_id", "")
        set_setting("kasse_vertretung_bis", "")
        flash("Kassenwart-Vertretung beendet.", "ok")
    else:
        mid = request.form.get("member_id", type=int)
        bis = parse_date(request.form.get("bis"))
        if not mid or not bis:
            flash("Bitte Person und Datum wählen.", "error")
        else:
            set_setting("kasse_vertretung_id", str(mid))
            set_setting("kasse_vertretung_bis", bis)
            m = get_db().execute("SELECT short, name FROM members WHERE id=?", (mid,)).fetchone()
            flash(f"{(m['short'] or m['name']) if m else 'Mitglied'} vertritt den Kassenwart bis {logic.date_de(bis)}.", "ok")
    return redirect(request.referrer or url_for("main.kasse"))


# =============================================================================
# Abstimmungen
# =============================================================================

def _poll_visible(p):
    return p["audience"] == "alle" or auth.can("vorstand")


def _poll_closed(p):
    if p["status"] == "geschlossen":
        return True
    if p["closes_at"]:
        return datetime.now().strftime("%Y-%m-%dT%H:%M") >= p["closes_at"]
    return False


def _has_voted(pid):
    uid = g.user.get("id")
    return bool(uid) and get_db().execute("SELECT 1 FROM poll_voters WHERE poll_id=? AND member_id=?",
                                          (pid, uid)).fetchone() is not None


def _open_polls_for_me():
    uid = g.user.get("id")
    if not uid:
        return []
    rows = get_db().execute(
        "SELECT * FROM polls p WHERE status='offen' AND NOT EXISTS "
        "(SELECT 1 FROM poll_voters v WHERE v.poll_id=p.id AND v.member_id=?) ORDER BY created_at DESC", (uid,)).fetchall()
    return [p for p in rows if _poll_visible(p) and not _poll_closed(p)]


def _poll_results(p):
    db = get_db()
    opts = db.execute("SELECT * FROM poll_options WHERE poll_id=? ORDER BY sort, id", (p["id"],)).fetchall()
    voters = db.execute("SELECT v.*, m.short, m.name FROM poll_voters v LEFT JOIN members m ON m.id=v.member_id "
                        "WHERE v.poll_id=? ORDER BY v.voted_at", (p["id"],)).fetchall()
    res = []
    for o in opts:
        rows = db.execute("SELECT v.member_id, m.short, m.name FROM poll_votes v LEFT JOIN members m ON m.id=v.member_id "
                          "WHERE v.option_id=?", (o["id"],)).fetchall()
        res.append({"option": o, "count": len(rows),
                    "names": [] if p["anonymous"] else [r["short"] or r["name"] for r in rows if r["member_id"]]})
    total_voters = len(voters)
    top = max((r["count"] for r in res), default=0)
    for r in res:
        r["pct"] = round(100 * r["count"] / total_voters) if total_voters else 0
        r["top"] = top > 0 and r["count"] == top
    audience_q = ("SELECT m.* FROM members m JOIN member_roles r ON r.member_id=m.id WHERE r.role='vorstand' AND m.status='aktiv'"
                  if p["audience"] == "vorstand" else "SELECT * FROM members WHERE status='aktiv'")
    voted_ids = {v["member_id"] for v in voters}
    missing = [m for m in db.execute(audience_q).fetchall() if m["id"] not in voted_ids]
    return {"options": res, "voters": voters, "total": total_voters, "missing": missing}


@bp.app_template_global()
def poll_status(p):
    return "geschlossen" if _poll_closed(p) else "offen"


@bp.route("/abstimmungen")
def abstimmungen():
    db = get_db()
    rows = [p for p in db.execute("SELECT * FROM polls ORDER BY created_at DESC LIMIT 60").fetchall() if _poll_visible(p)]
    open_, closed = [], []
    for p in rows:
        d = dict(p)
        d["voted"] = _has_voted(p["id"])
        d["n"] = db.execute("SELECT COUNT(*) FROM poll_voters WHERE poll_id=?", (p["id"],)).fetchone()[0]
        (closed if _poll_closed(p) else open_).append(d)
    return render_template("abstimmungen.html", open_polls=open_, closed_polls=closed)


@bp.route("/abstimmungen/<int:pid>")
def abstimmung(pid):
    p = get_db().execute("SELECT * FROM polls WHERE id=?", (pid,)).fetchone()
    if not p or not _poll_visible(p):
        abort(404)
    if p["kind"] == "wahl" and not p["finalized_at"]:
        from . import wahl
        if wahl.finalize(get_db(), p):
            p = get_db().execute("SELECT * FROM polls WHERE id=?", (pid,)).fetchone()
    closed = _poll_closed(p)
    voted = _has_voted(pid)
    results = _poll_results(p)
    show = (p["show_results"] == "sofort" or closed or (p["show_results"] == "nach_abstimmung" and voted))
    my_options = set()
    if voted and not p["anonymous"]:
        my_options = {r["option_id"] for r in get_db().execute(
            "SELECT option_id FROM poll_votes WHERE poll_id=? AND member_id=?", (pid, g.user["id"]))}
    event = get_db().execute("SELECT * FROM events WHERE id=?", (p["event_id"],)).fetchone() if p["event_id"] else None
    may_manage = (g.user.get("id") == p["created_by_id"]) or auth.can("vorstand") or auth.can("admin")
    return render_template("abstimmung.html", p=p, closed=closed, voted=voted, results=results, show=show,
                           my_options=my_options, event=event, may_manage=may_manage)


@bp.route("/abstimmungen/<int:pid>/stimme", methods=["POST"])
def abstimmung_stimme(pid):
    db = get_db()
    p = db.execute("SELECT * FROM polls WHERE id=?", (pid,)).fetchone()
    uid = g.user.get("id")
    if not p or not _poll_visible(p) or not uid:
        abort(404)
    if _poll_closed(p):
        abort(400, "Die Abstimmung ist beendet.")
    valid = {o["id"] for o in db.execute("SELECT id FROM poll_options WHERE poll_id=?", (pid,))}
    chosen = [o for o in request.form.getlist("option", type=int) if o in valid]
    if p["kind"] == "single":
        chosen = chosen[:1]
    elif p["kind"] == "wahl" and len(chosen) > (p["seats"] or 1):
        flash(f"Du kannst höchstens {p['seats'] or 1} Person(en) wählen.", "error")
        return redirect(url_for("main.abstimmung", pid=pid))
    if not chosen:
        flash("Bitte eine Auswahl treffen.", "error")
        return redirect(url_for("main.abstimmung", pid=pid))
    if p["anonymous"]:
        if _has_voted(pid):
            abort(400, "Bei anonymen Abstimmungen kann man nur einmal abstimmen.")
        for o in chosen:
            db.execute("INSERT INTO poll_votes(poll_id, option_id, member_id) VALUES (?,?,NULL)", (pid, o))
    else:
        db.execute("DELETE FROM poll_votes WHERE poll_id=? AND member_id=?", (pid, uid))
        for o in chosen:
            db.execute("INSERT INTO poll_votes(poll_id, option_id, member_id) VALUES (?,?,?)", (pid, o, uid))
    db.execute("INSERT INTO poll_voters(poll_id, member_id) VALUES (?,?) ON CONFLICT(poll_id, member_id) DO UPDATE SET voted_at=datetime('now')",
               (pid, uid))
    db.commit()
    flash("Stimme gespeichert.", "ok")
    if p["kind"] == "wahl":
        from . import wahl
        if wahl.finalize(db, db.execute("SELECT * FROM polls WHERE id=?", (pid,)).fetchone()):
            flash("Alle haben abgestimmt – die Wahl ist ausgewertet.", "ok")
    return redirect(url_for("main.abstimmung", pid=pid))


@bp.route("/abstimmungen/neu", methods=["GET", "POST"])
def abstimmung_form():
    db = get_db()
    if not g.user.get("id"):
        abort(403, "Nur Mitglieder können Abstimmungen starten.")
    if request.method == "POST":
        f = request.form
        title = (f.get("title") or "").strip()[:150]
        kind = f.get("kind") if f.get("kind") in ("single", "multi") else "single"
        audience = "vorstand" if f.get("audience") == "vorstand" and auth.can("vorstand") else "alle"
        show = f.get("show_results") if f.get("show_results") in ("sofort", "nach_abstimmung", "nach_ende") else "nach_abstimmung"
        closes = (f.get("closes_at") or "").strip()[:16] or None
        options = [o.strip()[:120] for o in f.getlist("options") if o.strip()]
        event_id = f.get("event_id", type=int) or None
        if not title or len(options) < 2:
            flash("Bitte eine Frage und mindestens zwei Antworten angeben.", "error")
        else:
            pid = db.execute(
                "INSERT INTO polls(title, description, kind, audience, anonymous, show_results, closes_at, event_id, created_by_id, created_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (title, (f.get("description") or "").strip()[:1000], kind, audience, 1 if f.get("anonymous") else 0,
                 show, closes, event_id, g.user["id"], actor())).lastrowid
            for i, o in enumerate(options):
                db.execute("INSERT INTO poll_options(poll_id, label, sort) VALUES (?,?,?)", (pid, o, i))
            db.commit()
            from . import notify
            notify.on_new_poll(pid, title, audience, g.user["id"], actor())
            flash("Abstimmung gestartet.", "ok")
            return redirect(url_for("main.abstimmung", pid=pid))
    events = db.execute("SELECT * FROM events WHERE date >= ? AND cancelled=0 ORDER BY date LIMIT 12",
                        (date.today().isoformat(),)).fetchall()
    preset = request.args.get("vorlage", "")
    pre_event = request.args.get("termin", type=int)
    return render_template("abstimmung_form.html", events=events, preset=preset, pre_event=pre_event)


@bp.route("/abstimmungen/<int:pid>/beenden", methods=["POST"])
def abstimmung_beenden(pid):
    db = get_db()
    p = db.execute("SELECT * FROM polls WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    if not (g.user.get("id") == p["created_by_id"] or auth.can("vorstand") or auth.can("admin")):
        abort(403)
    if request.form.get("delete"):
        db.execute("DELETE FROM polls WHERE id=?", (pid,))
        db.commit()
        flash("Abstimmung gelöscht.", "ok")
        return redirect(url_for("main.abstimmungen"))
    if p["kind"] == "wahl":
        from . import wahl
        msg = wahl.finalize(db, p, force=True)
        flash(f"Wahl beendet. {msg or ''}", "ok")
        return redirect(url_for("main.abstimmung", pid=pid))
    db.execute("UPDATE polls SET status='geschlossen' WHERE id=?", (pid,))
    db.commit()
    flash("Abstimmung beendet.", "ok")
    return redirect(url_for("main.abstimmung", pid=pid))


# =============================================================================
# Vorstandsbereich (nur Rolle "vorstand" – auch Admins ohne Vorstandsrolle sehen nichts)
# =============================================================================

@bp.route("/vorstand", methods=["GET", "POST"])
@require("vorstand")
def vorstand():
    db = get_db()
    if request.method == "POST":
        f = request.form
        nid = f.get("id", type=int)
        if f.get("delete") and nid:
            db.execute("DELETE FROM vorstand_notes WHERE id=?", (nid,))
        else:
            title = (f.get("title") or "").strip()[:150]
            body = (f.get("body") or "").strip()[:20000]
            pinned = 1 if f.get("pinned") else 0
            if title:
                if nid:
                    db.execute("UPDATE vorstand_notes SET title=?, body=?, pinned=?, updated_at=datetime('now') WHERE id=?",
                               (title, body, pinned, nid))
                else:
                    db.execute("INSERT INTO vorstand_notes(title, body, pinned, created_by) VALUES (?,?,?,?)",
                               (title, body, pinned, actor()))
        db.commit()
        flash("Gespeichert.", "ok")
        return redirect(url_for("main.vorstand"))
    notes = db.execute("SELECT * FROM vorstand_notes ORDER BY pinned DESC, COALESCE(updated_at, created_at) DESC").fetchall()
    polls = db.execute("SELECT * FROM polls WHERE audience='vorstand' ORDER BY created_at DESC LIMIT 20").fetchall()
    board = db.execute("SELECT m.* FROM members m JOIN member_roles r ON r.member_id=m.id WHERE r.role='vorstand' ORDER BY m.sort").fetchall()
    accounts = [a for a in logic.all_accounts(g.settings) if a["open"] >= 30]
    edit = db.execute("SELECT * FROM vorstand_notes WHERE id=?", (request.args.get("edit", type=int),)).fetchone() \
        if request.args.get("edit") else None
    return render_template("vorstand.html", notes=notes, polls=polls, board=board, debtors=accounts,
                           edit=edit, members=active_members(), vertretung=_vertretung_info(),
                           projects=[p for p in _visible_projects() if p["in_team"]])


@bp.app_template_global()
def nav_todo():
    """Offene Rückmeldungen für die Navigation (Badge)."""
    if not g.get("user") or not g.user.get("id"):
        return {"termine": 0, "polls": 0}
    if "_nav_todo" not in g:
        g._nav_todo = {"termine": len(_pending_rsvps()), "polls": len(_open_polls_for_me())}
    return g._nav_todo


# --- Testmodus: Ansicht als anderes Mitglied ----------------------------------

@bp.app_template_global()
def dev_members():
    if not auth.dev_mode():
        return []
    return get_db().execute("SELECT id, name, short, number FROM members WHERE status IN ('aktiv','ehren') "
                            "ORDER BY CASE status WHEN 'aktiv' THEN 0 ELSE 1 END, sort, name").fetchall()


@bp.route("/dev/als", methods=["POST"])
def dev_als():
    if not auth.dev_mode():
        abort(404)
    mid = request.form.get("member_id", type=int)
    if mid:
        session["dev_as"] = mid
    else:
        session.pop("dev_as", None)
    return redirect(request.referrer or url_for("main.start"))


@bp.app_template_global()
def dev_as():
    return session.get("dev_as") if auth.dev_mode() else None


# =============================================================================
# Kasse: Jahresübersicht, CSV-Import, Sicherung & Zurücksetzen
# =============================================================================

MONTHS_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


@bp.route("/kasse/jahr")
@bp.route("/kasse/jahr/<int:year>")
def kasse_jahr(year=None):
    db = get_db()
    s = g.settings
    year = year or date.today().year
    start_year = int((s.get("kasse_start") or str(date.today().year))[:4])
    members = db.execute(
        "SELECT * FROM members WHERE status='aktiv' OR id IN (SELECT member_id FROM penalties UNION SELECT member_id FROM payments) "
        "ORDER BY CASE status WHEN 'aktiv' THEN 0 WHEN 'ehren' THEN 1 ELSE 2 END, sort, name").fetchall()
    cur_year = date.today().year
    rows = []
    totals = {f"{year}-{m:02d}": 0.0 for m in range(1, 13)}
    for m in members:
        grid = logic.member_grid(m, s, year)
        for mo, c in grid.items():
            if c["state"] in ("open", "partial"):
                totals[mo] += c["due"] - c["paid"]
            totals[mo] += sum(p["amount"] for p in c["pens"] if not p["paid"])
        if m["status"] != "aktiv" and not any(c["state"] != "none" or c["pens"] for c in grid.values()):
            continue
        if year == cur_year:
            open_ = logic.member_account(m, s)["open"]           # echter Kontostand inkl. Vorjahre
        elif year < cur_year:                                    # was in diesem Jahr offen geblieben ist
            open_ = sum((c["due"] - c["paid"]) if c["state"] in ("open", "partial") else 0 for c in grid.values()) + \
                sum(p["amount"] for c in grid.values() for p in c["pens"] if not p["paid"])
        else:
            open_ = None                                         # Zukunft: nur Vorauszahlungen anzeigen
        rows.append({"m": m, "grid": grid, "open": open_})
    # Folgejahr nur anbieten, wenn schon jemand dafür vorausbezahlt hat
    years = list(range(start_year, max(cur_year, start_year) + 1))
    nxt = cur_year + 1
    if any(c["state"] == "future_paid" for m in members for c in logic.member_grid(m, s, nxt).values()):
        years.append(nxt)
    if year not in years:
        years.append(year)
        years.sort()
    return render_template("kasse_jahr.html", rows=rows, year=year, years=years, months=MONTHS_DE, totals=totals,
                           current=logic.ym(date.today()), cur_year=cur_year)


def backup_db(reason):
    """Kopie der Datenbank in /data/backups (die letzten 20 bleiben)."""
    import sqlite3
    from flask import current_app
    folder = os.path.join(current_app.config["DATA_DIR"], "backups")
    os.makedirs(folder, exist_ok=True)
    name = f"kcu-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{reason}.db"
    src = get_db()
    dst = sqlite3.connect(os.path.join(folder, name))
    src.commit()
    src.backup(dst)
    dst.close()
    files = sorted(f for f in os.listdir(folder) if f.endswith(".db"))
    for old in files[:-20]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return name


def _parse_csv_import(text):
    import csv
    import io
    from . import kstats
    text = text.lstrip("﻿")
    sample = text[:2000]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    res = kstats.Resolver()
    rows, errors = [], []
    for i, raw in enumerate(reader, 1):
        cells = [c.strip() for c in raw]
        if not cells or not any(cells) or cells[0].startswith("#"):
            continue
        if cells[0].lower() in ("typ", "art", "type"):
            continue
        cells += [""] * (6 - len(cells))
        typ, d, name, amount, text_, monat = cells[:6]
        typ = typ.lower()
        typ = {"z": "zahlung", "zahlung": "zahlung", "s": "strafe", "strafe": "strafe", "e": "einnahme",
               "einnahme": "einnahme", "a": "ausgabe", "ausgabe": "ausgabe"}.get(typ)
        err = []
        if not typ:
            err.append("Typ unbekannt (zahlung/strafe/einnahme/ausgabe)")
        dd = parse_date(d)
        if not dd:
            try:
                dd = datetime.strptime(d, "%d.%m.%Y").date().isoformat()
            except ValueError:
                try:
                    dd = datetime.strptime(d, "%d.%m.%y").date().isoformat()
                except ValueError:
                    err.append("Datum ungültig")
        amt = parse_amount(amount)
        if amt is None or amt <= 0:
            err.append("Betrag ungültig")
        mid = None
        if typ in ("zahlung", "strafe"):
            mid = res.member_id(name)
            if not mid:
                err.append(f"Mitglied „{name}“ unbekannt")
        period = None
        if typ == "zahlung":
            period = parse_period(monat, dd)
            if monat and not period:
                err.append("Monat ungültig")
            if not monat:   # „Beitrag Juli – …“ im Text
                mt = re.match(r"beitrag\s+([a-zäöü]+)", text_.lower())
                period = parse_period(mt.group(1), dd) if mt else None
        rows.append({"line": i, "typ": typ, "date": dd, "name": name, "member": res.label(name) if mid else name,
                     "member_id": mid, "amount": amt, "text": text_[:200], "period": period, "errors": err})
        if err:
            errors.append(i)
    return rows, errors


@bp.route("/kasse/import", methods=["GET", "POST"])
def kasse_import():
    if not (auth.can("kasse") or auth.can("admin")):
        abort(403, "Den Import machen Kassenwart, Vorstand oder Admin.")
    db = get_db()
    rows, errors, raw = [], [], ""
    if request.method == "POST":
        f = request.files.get("file")
        raw = request.form.get("raw") or ""
        if f and f.filename:
            data = f.read(5 * 1024 * 1024)
            for enc in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    raw = data.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
        rows, errors = _parse_csv_import(raw)
        if request.form.get("step") == "import" and rows:
            if errors:
                flash("Bitte erst die markierten Zeilen korrigieren.", "error")
            else:
                bk = backup_db("vor-import")
                if request.form.get("set_start") and len(request.form["set_start"]) == 7:
                    set_setting("kasse_start", request.form["set_start"])
                n = {"zahlung": 0, "strafe": 0, "einnahme": 0, "ausgabe": 0}
                for r in rows:
                    if r["typ"] == "zahlung":
                        low = (r["text"] or "").lower()
                        method = ("paypal" if "paypal" in low else "verrechnet" if "verrechnet" in low
                                  else "ueberweisung" if "überweisung" in low or "ueberweisung" in low
                                  else "bar" if re.search(r"\bbar\b", low) else None)
                        db.execute("INSERT INTO payments(member_id, date, amount, note, method, period, created_by) VALUES (?,?,?,?,?,?,?)",
                                   (r["member_id"], r["date"], r["amount"], r["text"] or "Import", method, r["period"], actor() + " (Import)"))
                    elif r["typ"] == "strafe":
                        db.execute("INSERT INTO penalties(member_id, kind, title, amount, date, note, created_by) "
                                   "VALUES (?, 'strafe', ?, ?, ?, 'Import', ?)",
                                   (r["member_id"], r["text"] or "Strafe", r["amount"], r["date"], actor() + " (Import)"))
                    else:
                        db.execute("INSERT INTO ledger(date, kind, amount, purpose, created_by) VALUES (?,?,?,?,?)",
                                   (r["date"], r["typ"], r["amount"], r["text"] or "Import", actor() + " (Import)"))
                    n[r["typ"]] += 1
                db.commit()
                flash(f"Importiert: {n['zahlung']} Zahlungen, {n['strafe']} Strafen, {n['einnahme'] + n['ausgabe']} Kassenbuch-Einträge. "
                      f"Sicherung vorher: {bk}", "ok")
                return redirect(url_for("main.kasse"))
    first = min((r["date"] for r in rows if r["date"] and r["typ"] in ("zahlung", "strafe")), default=None)
    start_hint = first[:7] if first and first[:7] < (g.settings.get("kasse_start") or "9999") else None
    return render_template("kasse_import.html", rows=rows, errors=errors, raw=raw, start_hint=start_hint,
                           total={t: sum(r["amount"] or 0 for r in rows if r["typ"] == t) for t in ("zahlung", "strafe", "einnahme", "ausgabe")})


RESET_PARTS = {
    "strafen": ("Alle Strafen", ["DELETE FROM penalties"]),
    "zahlungen": ("Alle Zahlungen", ["DELETE FROM payments"]),
    "kassenbuch": ("Kassenbuch (ohne Projekt-Buchungen)", ["DELETE FROM ledger WHERE project_id IS NULL"]),
    "termine": ("Alle Termine inkl. Zu-/Absagen (Sitzungen werden neu angelegt)",
                ["DELETE FROM event_rsvp", "UPDATE polls SET event_id=NULL", "DELETE FROM events"]),
    "rsvp": ("Nur Zu-/Absagen", ["DELETE FROM event_rsvp"]),
    "abstimmungen": ("Alle Abstimmungen", ["DELETE FROM polls"]),
    "kicker": ("Alle Kicker-Turniere (Import und eigene)", ["DELETE FROM kt_tournaments"]),
}


@bp.route("/admin/zuruecksetzen", methods=["GET", "POST"])
@require("admin")
def admin_reset():
    db = get_db()
    counts = {
        "strafen": db.execute("SELECT COUNT(*) FROM penalties").fetchone()[0],
        "zahlungen": db.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
        "kassenbuch": db.execute("SELECT COUNT(*) FROM ledger WHERE project_id IS NULL").fetchone()[0],
        "termine": db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "rsvp": db.execute("SELECT COUNT(*) FROM event_rsvp").fetchone()[0],
        "abstimmungen": db.execute("SELECT COUNT(*) FROM polls").fetchone()[0],
        "kicker": db.execute("SELECT COUNT(*) FROM kt_tournaments").fetchone()[0],
    }
    if request.method == "POST":
        parts = [p for p in request.form.getlist("parts") if p in RESET_PARTS]
        if (request.form.get("confirm") or "").strip().upper() != "LÖSCHEN":
            flash("Zur Sicherheit bitte LÖSCHEN in das Feld schreiben.", "error")
        elif not parts:
            flash("Nichts ausgewählt.", "error")
        else:
            bk = backup_db("vor-reset")
            for p in parts:
                for sql in RESET_PARTS[p][1]:
                    db.execute(sql)
            if "kasse_start" in request.form and request.form.get("kasse_start"):
                set_setting("kasse_start", request.form["kasse_start"][:7])
            db.commit()
            flash(f"Zurückgesetzt: {', '.join(RESET_PARTS[p][0] for p in parts)}. Sicherung: {bk}", "ok")
            return redirect(url_for("main.admin_reset"))
    from flask import current_app
    folder = os.path.join(current_app.config["DATA_DIR"], "backups")
    backups = sorted((f for f in os.listdir(folder) if f.endswith(".db")), reverse=True)[:10] if os.path.isdir(folder) else []
    return render_template("admin/reset.html", parts=RESET_PARTS, counts=counts, backups=backups)


# =============================================================================
# Benachrichtigungen (Push & E-Mail)
# =============================================================================

@bp.route("/sw.js")
def service_worker():
    from flask import current_app
    resp = send_from_directory(os.path.join(current_app.root_path, "static"), "sw.js", max_age=0)
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/benachrichtigungen", methods=["GET", "POST"])
def benachrichtigungen():
    from . import notify
    db = get_db()
    uid = g.user.get("id")
    if request.method == "POST":
        if not uid:
            abort(403, "Nur für Mitglieder.")
        if "birthday" in request.form:
            db.execute("UPDATE members SET birthday=? WHERE id=?", (parse_date(request.form.get("birthday")), uid))
        db.execute("INSERT INTO notify_prefs(member_id, push_on, email_on) VALUES (?,?,?) "
                   "ON CONFLICT(member_id) DO UPDATE SET push_on=excluded.push_on, email_on=excluded.email_on",
                   (uid, 1 if request.form.get("push_on") else 0, 1 if request.form.get("email_on") else 0))
        db.commit()
        flash("Gespeichert.", "ok")
        return redirect(url_for("main.benachrichtigungen"))
    pref = db.execute("SELECT * FROM notify_prefs WHERE member_id=?", (uid,)).fetchone() if uid else None
    subs = db.execute("SELECT id, label, created_at FROM push_subs WHERE member_id=? ORDER BY created_at DESC", (uid,)).fetchall() if uid else []
    me = db.execute("SELECT * FROM members WHERE id=?", (uid,)).fetchone() if uid else None
    return render_template("benachrichtigungen.html", pref=pref, subs=subs, vapid_key=notify.vapid().public_b64, me=me,
                           smtp_ok=notify.smtp_config(g.settings)["ok"],
                           my_email=(db.execute("SELECT email FROM members WHERE id=?", (uid,)).fetchone() or {"email": None})["email"] if uid else None)


@bp.route("/push/abo", methods=["POST"])
def push_subscribe():
    from . import notify
    uid = g.user.get("id")
    data = request.get_json(silent=True) or {}
    sub = data.get("subscription") or {}
    endpoint = (sub.get("endpoint") or "")[:800]
    keys = sub.get("keys") or {}
    if not uid:
        return jsonify({"ok": False, "error": "Nur für Mitglieder"}), 403
    if not notify.valid_endpoint(endpoint) or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"ok": False, "error": "Ungültiges Abo"}), 400
    label = (data.get("label") or request.headers.get("User-Agent") or "")[:120]
    db = get_db()
    db.execute("INSERT INTO push_subs(member_id, endpoint, p256dh, auth, label) VALUES (?,?,?,?,?) "
               "ON CONFLICT(endpoint) DO UPDATE SET member_id=excluded.member_id, p256dh=excluded.p256dh, auth=excluded.auth, label=excluded.label",
               (uid, endpoint, keys["p256dh"][:200], keys["auth"][:100], label))
    db.execute("INSERT OR IGNORE INTO notify_prefs(member_id) VALUES (?)", (uid,))
    db.execute("UPDATE notify_prefs SET push_on=1 WHERE member_id=?", (uid,))
    db.commit()
    return jsonify({"ok": True})


@bp.route("/push/abmelden", methods=["POST"])
def push_unsubscribe():
    uid = g.user.get("id")
    data = request.get_json(silent=True) or {}
    db = get_db()
    if data.get("endpoint"):
        db.execute("DELETE FROM push_subs WHERE endpoint=? AND member_id=?", (data["endpoint"], uid))
    elif request.form.get("id"):
        db.execute("DELETE FROM push_subs WHERE id=? AND member_id=?", (request.form.get("id", type=int), uid))
    db.commit()
    if request.is_json:
        return jsonify({"ok": True})
    flash("Gerät abgemeldet.", "ok")
    return redirect(url_for("main.benachrichtigungen"))


@bp.route("/admin/testmail", methods=["POST"])
@require("admin")
def admin_testmail():
    import smtplib
    from . import notify
    to = (request.form.get("to") or "").strip()[:200]
    if "@" not in to:
        flash("Bitte eine E-Mail-Adresse angeben.", "error")
        return redirect(url_for("main.admin_settings"))
    try:
        title, body = "Testmail", "Der Mailversand der Vereins-App funktioniert. So sehen künftig Erinnerungen an Termine, Fristen und Abstimmungen aus."
        notify.send_mail(to, "KCU · Testmail", notify.mail_text(title, body, "/", g.settings), raise_errors=True,
                         html=notify.mail_html(title, body, "/", g.settings))
        flash(f"Testmail an {to} verschickt – schau auch in den Spam-Ordner.", "ok")
    except smtplib.SMTPAuthenticationError:
        flash("Anmeldung abgelehnt: Benutzername oder Passwort falsch. Bei Gmail muss es ein App-Passwort sein "
              "(nicht das normale Passwort), gesetzt als Container-Variable KCU_SMTP_PASS.", "error")
    except Exception as e:  # noqa: BLE001
        flash(f"Versand fehlgeschlagen: {type(e).__name__}: {str(e)[:200]}", "error")
    return redirect(url_for("main.admin_settings"))


@bp.route("/push/test", methods=["POST"])
def push_test():
    from . import notify
    uid = g.user.get("id")
    if not uid:
        abort(403)
    n = notify.deliver([uid], "👋 Testnachricht", "Wenn du das liest, kommen Benachrichtigungen der KCU-App bei dir an.",
                       "/benachrichtigungen", force=True)
    flash("Testnachricht verschickt – kommt gleich an." if n else
          "Nichts angekommen: Kein Gerät angemeldet oder Push/Mail ausgeschaltet.", "ok" if n else "error")
    return redirect(url_for("main.benachrichtigungen"))


# =============================================================================
# Jahresrückblick („Wrapped“) – ab Silvester
# =============================================================================

@bp.app_template_global()
def wrapped_year():
    t = date.today()
    if t.month == 12 and t.day == 31:
        return t.year
    if t.month == 1:
        return t.year - 1
    return None


def _wrapped_available(year):
    t = date.today()
    return year < t.year or (year == t.year and t.month == 12 and t.day == 31)


def _wrapped_data(year, mid):
    from . import kstats
    db = get_db()
    y = str(year)
    today = date.today().isoformat()
    members = {r["id"]: r for r in db.execute("SELECT * FROM members")}
    me = members.get(mid)
    d = {"year": year, "me": me}
    # Sitzungen & Termine
    sessions = db.execute("SELECT id FROM events WHERE kind='sitzung' AND cancelled=0 AND substr(date,1,4)=? AND date<=?",
                          (y, today)).fetchall()
    sids = [r["id"] for r in sessions]
    d["n_sessions"] = len(sids)
    yes_counts = {}
    for r in db.execute("SELECT r.member_id, COUNT(*) c FROM event_rsvp r JOIN events e ON e.id=r.event_id "
                        "WHERE r.status='ja' AND substr(e.date,1,4)=? AND e.date<=? GROUP BY r.member_id", (y, today)):
        yes_counts[r["member_id"]] = r["c"]
    d["yes"] = yes_counts.get(mid, 0)
    d["no"] = db.execute("SELECT COUNT(*) FROM event_rsvp r JOIN events e ON e.id=r.event_id WHERE r.member_id=? AND r.status='nein' "
                         "AND substr(e.date,1,4)=?", (mid, y)).fetchone()[0]
    d["reasons"] = [r["reason"] for r in db.execute(
        "SELECT r.reason FROM event_rsvp r JOIN events e ON e.id=r.event_id WHERE r.member_id=? AND r.status='nein' "
        "AND substr(e.date,1,4)=? AND r.reason<>'' ORDER BY e.date", (mid, y))][:3]
    # Strafen
    pen_rows = db.execute("SELECT member_id, COUNT(*) c, COALESCE(SUM(amount),0) s FROM penalties WHERE substr(date,1,4)=? "
                          "GROUP BY member_id ORDER BY s DESC, c DESC", (y,)).fetchall()
    d["pen_rank"] = next((i for i, r in enumerate(pen_rows, 1) if r["member_id"] == mid), None)
    mine = next((r for r in pen_rows if r["member_id"] == mid), None)
    d["pen_count"] = mine["c"] if mine else 0
    d["pen_sum"] = mine["s"] if mine else 0
    d["pen_total"] = sum(r["s"] for r in pen_rows)
    d["pen_king"] = members.get(pen_rows[0]["member_id"]) if pen_rows else None
    top = db.execute("SELECT title, COUNT(*) c FROM penalties WHERE member_id=? AND substr(date,1,4)=? GROUP BY title "
                     "ORDER BY c DESC LIMIT 1", (mid, y)).fetchone()
    d["pen_top"] = top
    d["naturals"] = db.execute("SELECT COUNT(*) FROM penalties WHERE member_id=? AND substr(date,1,4)=? AND natural IS NOT NULL "
                               "AND natural<>''", (mid, y)).fetchone()[0]
    d["paid"] = db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE member_id=? AND substr(date,1,4)=?",
                           (mid, y)).fetchone()[0]
    vote_counts = {r["member_id"]: r["c"] for r in db.execute(
        "SELECT member_id, COUNT(*) c FROM poll_voters WHERE substr(voted_at,1,4)=? GROUP BY member_id", (y,))}
    d["votes"] = vote_counts.get(mid, 0)
    # Kicker
    res = kstats.Resolver(db)
    matches = kstats.load_matches("kicker", year, db=db, counted_only=True)
    stats, records = kstats.compute(matches, res, min_pair=3)
    tit = kstats.titles("kicker", year, db=db, resolver=res, counted_only=True)
    d["k"] = stats.get(mid)
    d["k_titles"] = tit.get(mid, {"gold": 0, "silver": 0, "bronze": 0, "won": []})
    d["k_matches"] = len(matches)
    d["k_goals"] = sum(m["score1"] + m["score2"] for m in matches)
    ranking = sorted(stats.items(), key=lambda kv: (-kv[1]["stars"], -kv[1]["win_pct"]))
    d["k_rank"] = next((i for i, (k, _) in enumerate(ranking, 1) if k == mid), None)
    d["k_mvp"] = members.get(ranking[0][0]) if ranking else None
    d["k_mvp_stars"] = ranking[0][1]["stars"] if ranking else 0
    d["members"] = members
    # Titel des Jahres
    badges = []
    def is_top(values, cond=lambda v: v > 0):
        if mid not in values or not cond(values[mid]):
            return False
        return values[mid] == max(values.values())
    star_v = {k: v["stars"] for k, v in stats.items()}
    min_g = max(5, len(matches) // 30)
    pct_v = {k: v["win_pct"] for k, v in stats.items() if v["games"] >= min_g}
    if is_top(star_v):
        badges.append(("👑", "Kicker-König", f"Die meisten Sterne {year}"))
    if pct_v and is_top(pct_v):
        badges.append(("🛡️", "Der Unbezwingbare", "Beste Siegquote im ganzen Verein"))
    if is_top({k: v["shutout_wins"] for k, v in stats.items()}):
        badges.append(("🧱", "Die Mauer", "Die meisten Zu-Null-Siege"))
    if is_top({k: v["kasten_losses"] for k, v in stats.items()}):
        badges.append(("🍺", "Kastenlieferant", "Die meisten 10:0-Pleiten – Prost!"))
    if pen_rows and pen_rows[0]["member_id"] == mid and pen_rows[0]["s"] > 0:
        badges.append(("💸", "Strafenkönig", "Niemand hat mehr in die Kasse gespült"))
    if is_top(yes_counts):
        badges.append(("🫶", "Der Treue", "Die meisten Zusagen"))
    if is_top(vote_counts):
        badges.append(("🗳️", "Demokrat des Jahres", "Bei den meisten Abstimmungen dabei"))
    if mine is None and pen_rows:
        badges.append(("😇", "Heiliger des Jahres", "Keine einzige Strafe!"))
    if d["k"] and d["k"]["best_streak"] >= 5:
        badges.append(("🔥", "Lauf des Jahres", f"{d['k']['best_streak']} Siege in Folge"))
    if not badges:
        badges.append(("❤️", "Herz des Vereins", "Ohne dich wär's nur halb so lustig"))
    d["badges"] = badges
    return d


@bp.route("/rueckblick")
@bp.route("/rueckblick/<int:year>")
def rueckblick(year=None):
    year = year or wrapped_year() or (date.today().year - 1)
    preview = request.args.get("vorschau") and auth.can("admin")
    if not _wrapped_available(year) and not preview:
        return render_template("rueckblick_wait.html", year=year,
                               days=(date(year, 12, 31) - date.today()).days)
    mid = g.user.get("id")
    if preview and request.args.get("mitglied", type=int):
        mid = request.args.get("mitglied", type=int)
    if not mid:
        flash("Der Rückblick ist pro Mitglied – als Admin ohne Mitgliedseintrag gibt's nur die Vorschau mit ?vorschau=1&mitglied=ID.", "error")
        return redirect(url_for("main.start"))
    return render_template("rueckblick.html", d=_wrapped_data(year, mid), preview=preview)


# =============================================================================
# Protokolle: mehrere PDFs auf einmal importieren
# =============================================================================

_DATE_PATTERNS = [
    (r"(?<!\d)(\d{4})[-_.](\d{1,2})[-_.](\d{1,2})(?!\d)", "ymd"),
    (r"(?<!\d)(\d{1,2})[._\- ](\d{1,2})[._\- ](\d{4}|\d{2})(?!\d)", "dmy"),
    (r"(?<!\d)(\d{2})(\d{2})(\d{4})(?!\d)", "dmy"),
]


def _find_date(text):
    text = text or ""
    for rx, kind in _DATE_PATTERNS:
        for m in re.finditer(rx, text):
            a, b, c = (int(x) for x in m.groups())
            y, mo, d = (a, b, c) if kind == "ymd" else (c + 2000 if c < 100 else c, b, a)
            try:
                return date(y, mo, d).isoformat() if 2015 <= y <= date.today().year + 1 else None
            except ValueError:
                continue
    # „Oktober 2024“ -> 2. Samstag des Monats (Sitzungstermin)
    m = re.search(r"(januar|februar|märz|maerz|april|mai|juni|juli|august|september|oktober|november|dezember)\s*(\d{4})",
                  text.lower())
    if m:
        name = m.group(1).replace("maerz", "märz")
        y, mo = int(m.group(2)), MONTH_NAMES.index(name) + 1
        if 2015 <= y <= date.today().year + 1:
            return logic.second_saturday(y, mo).isoformat()
    return None


def _pdf_text(path, pages=2):
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        return "\n".join((p.extract_text() or "") for p in r.pages[:pages])
    except Exception:  # noqa: BLE001 – Text ist nur eine Hilfe zur Datumserkennung
        return ""


@bp.route("/protokolle/import", methods=["GET", "POST"])
@require("redaktion")
def protokoll_import():
    db = get_db()
    results = []
    if request.method == "POST":
        for up in request.files.getlist("files"):
            if not up or not up.filename:
                continue
            fname = os.path.basename(up.filename)[:120]
            if db.execute("SELECT 1 FROM protocols WHERE file_name=?", (fname,)).fetchone():
                results.append({"file": fname, "state": "vorhanden"})
                continue
            try:
                stored = media.save_pdf_upload(up)
            except ValueError as e:
                results.append({"file": fname, "state": "fehler", "msg": str(e)})
                continue
            d = _find_date(os.path.splitext(fname)[0])
            how = "Dateiname"
            if not d:
                d = _find_date(_pdf_text(os.path.join(media.uploads_dir(), stored)))
                how = "PDF-Text"
            if not d:
                d, how = date.today().isoformat(), None
            stem = re.sub(r"[_]+", " ", os.path.splitext(fname)[0]).strip()
            title = f"Sitzung vom {logic.date_de(d)}" if how else stem[:150]
            pid = db.execute("INSERT INTO protocols(date, title, body, file, file_name, created_by) VALUES (?,?,?,?,?,?)",
                             (d, title, "", stored, fname, actor() + " (Import)")).lastrowid
            results.append({"file": fname, "state": "neu", "pid": pid, "date": d, "how": how, "title": title})
        db.commit()
        if results:
            n = sum(1 for r in results if r["state"] == "neu")
            flash(f"{n} Protokoll(e) importiert." + (" Bei manchen wurde kein Datum gefunden – bitte nachtragen."
                                                     if any(r.get("state") == "neu" and not r.get("how") for r in results) else ""), "ok")
    return render_template("protokoll_import.html", results=results)



# =============================================================================
# Kalender-Abo (ICS) – für Handy-, Google- und Outlook-Kalender
# =============================================================================

def _ics_escape(t):
    return (t or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _ics_fold(line):
    out, b = [], line.encode("utf-8")
    while len(b) > 74:
        cut = 74
        while cut > 0 and (b[cut] & 0xC0) == 0x80:   # UTF-8-Zeichen nicht zerschneiden
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n ".join(out)


def _cal_url(token):
    base = (g.settings.get("app_url") or request.host_url).rstrip("/")
    return f"{base}/kal/{token}.ics"


@bp.route("/kalender", methods=["GET", "POST"])
def kalender():
    import secrets
    uid = g.user.get("id")
    if not uid:
        abort(403, "Das Kalender-Abo gibt es für Mitglieder.")
    db = get_db()
    me = db.execute("SELECT * FROM members WHERE id=?", (uid,)).fetchone()
    token = me["cal_token"]
    if request.method == "POST" or not token:
        token = secrets.token_urlsafe(24)
        db.execute("UPDATE members SET cal_token=? WHERE id=?", (token, uid))
        db.commit()
        if request.method == "POST":
            flash("Neuer Kalender-Link erstellt – der alte funktioniert nicht mehr. Bitte im Kalender neu abonnieren.", "ok")
            return redirect(url_for("main.kalender"))
    url = _cal_url(token)
    webcal = "webcal://" + url.split("://", 1)[1]
    from urllib.parse import quote
    return render_template("kalender.html", url=url, webcal=webcal,
                           google=f"https://calendar.google.com/calendar/r?cid={quote(webcal, safe='')}")


@bp.route("/kal/<token>.ics")
def calendar_feed(token):
    from datetime import datetime as dt, timedelta as td
    from .db import get_settings
    db = get_db()
    if len(token) < 20:
        abort(404)
    me = db.execute("SELECT * FROM members WHERE cal_token=?", (token,)).fetchone()
    if not me or me["status"] == "ehemalig":
        abort(404)
    s = get_settings()
    club = s.get("club_short") or "KCU"
    base = (s.get("app_url") or request.host_url).rstrip("/")
    host = base.split("://", 1)[-1].split("/")[0] or "kcu"
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")
    except Exception:  # noqa: BLE001
        tz = None
    stamp = dt.utcnow().strftime("%Y%m%dT%H%M%SZ")
    since = (date.today() - td(days=180)).isoformat()
    mine = {r["event_id"]: r for r in db.execute("SELECT * FROM event_rsvp WHERE member_id=?", (me["id"],))}
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{club}//Vereins-App//DE", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
         f"X-WR-CALNAME:{_ics_escape(s.get('club_name') or 'Kicker Club United')}", "X-WR-TIMEZONE:Europe/Berlin",
         "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    for e in db.execute("SELECT * FROM events WHERE date IS NOT NULL AND date<>'' AND date>=? ORDER BY date", (since,)):
        try:
            d = date.fromisoformat(e["date"])
        except ValueError:
            continue
        L += ["BEGIN:VEVENT", f"UID:event-{e['id']}@{host}", f"DTSTAMP:{stamp}"]
        t = (e["time"] or "").strip()
        if t and len(t) >= 4 and ":" in t:
            try:
                hh, mm = (int(x) for x in t.split(":")[:2])
                start = dt(d.year, d.month, d.day, hh, mm, tzinfo=tz) if tz else dt(d.year, d.month, d.day, hh, mm)
                end = start + td(hours=3)
                if tz:
                    from datetime import timezone as _tz
                    fmt = lambda x: x.astimezone(_tz.utc).strftime("%Y%m%dT%H%M%SZ")   # noqa: E731
                else:
                    fmt = lambda x: x.strftime("%Y%m%dT%H%M%S")   # noqa: E731
                L += [f"DTSTART:{fmt(start)}", f"DTEND:{fmt(end)}"]
            except ValueError:
                t = ""
        if not t:
            L += [f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(d + td(days=1)).strftime('%Y%m%d')}"]
        title = e["title"] + (" (fällt aus)" if e["cancelled"] else "")
        L.append(f"SUMMARY:{_ics_escape(club + ': ' + title)}")
        desc = []
        if e["note"]:
            desc.append(e["note"])
        r = mine.get(e["id"])
        if e["rsvp"] and not e["cancelled"]:
            desc.append(f"Deine Antwort: {RSVP_LABEL.get(r['status'], r['status'])}" if r else
                        "Du hast noch nicht zu- oder abgesagt." + (f" Frist: {logic.date_de(e['rsvp_until'])}" if e["rsvp_until"] else ""))
        desc.append(f"{base}/termine/{e['id']}")
        L.append(f"DESCRIPTION:{_ics_escape(chr(10).join(desc))}")
        L.append(f"URL:{base}/termine/{e['id']}")
        if e["location"]:
            L.append(f"LOCATION:{_ics_escape(e['location'])}")
        L.append("STATUS:CANCELLED" if e["cancelled"] else "STATUS:CONFIRMED")
        L.append("END:VEVENT")
    # Geburtstage als jährliche Ganztagstermine
    for m in db.execute("SELECT * FROM members WHERE status IN ('aktiv','ehren') AND birthday IS NOT NULL AND birthday<>''"):
        try:
            b = date.fromisoformat(m["birthday"])
        except ValueError:
            continue
        first = b.replace(year=max(b.year, 2000)) if not (b.month == 2 and b.day == 29) else date(max(b.year, 2000), 2, 29)
        L += ["BEGIN:VEVENT", f"UID:bday-{m['id']}@{host}", f"DTSTAMP:{stamp}",
              f"DTSTART;VALUE=DATE:{first.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(first + td(days=1)).strftime('%Y%m%d')}",
              "RRULE:FREQ=YEARLY", f"SUMMARY:{_ics_escape('🎂 ' + (m['short'] or m['name']) + ' hat Geburtstag')}",
              "TRANSP:TRANSPARENT", "END:VEVENT"]
    L.append("END:VCALENDAR")
    body = "\r\n".join(_ics_fold(x) for x in L) + "\r\n"
    from flask import Response
    resp = Response(body, mimetype="text/calendar")
    resp.headers["Content-Disposition"] = f'inline; filename="{club.lower()}.ics"'
    resp.headers["Cache-Control"] = "private, max-age=900"
    return resp



# =============================================================================
# Profil (sichtbare Angaben) – getrennt von Account (Anmeldung) und Rollen (Rechte)
# =============================================================================

POSITIONS = {"": "–", "sturm": "Sturm", "abwehr": "Abwehr", "beides": "Allrounder"}

RIGHTS = [
    ("immer", "Termine sehen, zu- und absagen, abstimmen, Strafen eintragen, Turniere spielen, Protokolle hochladen"),
    ("redaktion", "Protokolle bearbeiten und löschen, Museum und Satzung pflegen"),
    ("kasse", "Zahlungen, Kosten und Kassenbuch, Strafen löschen"),
    ("termine", "Termine anlegen, verschieben und absagen"),
    ("vorstand", "Vorstandsbereich und Vorstandsabstimmungen"),
    ("kicker_verwalten", "Kicker-Turniere importieren, Namen zuordnen"),
    ("admin", "Mitglieder, Rollen, Strafenkatalog und Einstellungen verwalten"),
]


def _rights_for(member_id):
    """Rechte eines (beliebigen) Mitglieds – rechnet mit dessen Rollen, nicht mit denen des Betrachters."""
    roles = auth.roles_of(member_id)
    m = get_db().execute("SELECT email FROM members WHERE id=?", (member_id,)).fetchone()
    if m and m["email"] and m["email"].lower() in auth.admin_emails():
        roles.add("admin")
    vertretung = (g.settings.get("kasse_vertretung_id") == str(member_id) and
                  date.today().isoformat() <= (g.settings.get("kasse_vertretung_bis") or ""))
    has = {
        "immer": True,
        "redaktion": bool(roles & {"redaktion", "vorstand", "admin"}),
        "kasse": bool(roles & {"kassenwart", "vorstand"}) or vertretung,
        "termine": bool(roles & {"admin", "vorstand"}),
        "vorstand": "vorstand" in roles,
        "kicker_verwalten": bool(roles & {"admin", "vorstand"}),
        "admin": "admin" in roles,
    }
    return roles, [(label, has[k]) for k, label in RIGHTS], vertretung


@bp.app_template_global()
def avatar_url(m):
    try:
        a = m["avatar"]
    except (KeyError, IndexError, TypeError):
        a = None
    return url_for("main.files", name=a) if a else None


@bp.route("/profil", methods=["GET", "POST"])
def profil():
    uid = g.user.get("id")
    if not uid:
        flash("Als Admin ohne Mitgliedseintrag hast du kein Profil – lege dich unter Admin → Mitglieder an.", "error")
        return redirect(url_for("main.start"))
    db = get_db()
    me = db.execute("SELECT * FROM members WHERE id=?", (uid,)).fetchone()
    if request.method == "POST":
        f = request.form
        short = " ".join((f.get("short") or "").split())[:40] or me["short"]
        bio = (f.get("bio") or "").strip()[:200]
        position = f.get("position") if f.get("position") in POSITIONS else ""
        birthday = parse_date(f.get("birthday"))
        avatar = me["avatar"]
        up = request.files.get("avatar")
        if up and up.filename:
            try:
                new = media.save_avatar(up)
                if avatar:
                    media.delete_upload(avatar)
                avatar = new
            except Exception:  # noqa: BLE001
                flash("Das Bild konnte nicht gelesen werden (JPG, PNG oder HEIC).", "error")
                return redirect(url_for("main.profil"))
        if f.get("remove_avatar") and avatar:
            media.delete_upload(avatar)
            avatar = None
        db.execute("UPDATE members SET short=?, bio=?, position=?, birthday=?, avatar=? WHERE id=?",
                   (short, bio, position, birthday, avatar, uid))
        # Spitznamen = Namen, unter denen man in Kickertool-Turnieren auftaucht
        if "nicknames" in f:
            wanted = {" ".join(x.split()).lower()[:40] for x in f.get("nicknames", "").split(",") if x.strip()}
            mine = {r["alias"] for r in db.execute("SELECT alias FROM player_aliases WHERE member_id=?", (uid,))}
            for a in mine - wanted:
                db.execute("DELETE FROM player_aliases WHERE alias=? AND member_id=?", (a, uid))
            taken = []
            for a in wanted - mine:
                row = db.execute("SELECT member_id, ignore FROM player_aliases WHERE alias=?", (a,)).fetchone()
                if row and row["member_id"] and row["member_id"] != uid:
                    taken.append(a)
                    continue
                db.execute("INSERT INTO player_aliases(alias, member_id, ignore) VALUES (?,?,0) "
                           "ON CONFLICT(alias) DO UPDATE SET member_id=excluded.member_id, ignore=0", (a, uid))
            if taken:
                flash("Schon einem anderen Mitglied zugeordnet: " + ", ".join(taken), "error")
        db.commit()
        flash("Profil gespeichert.", "ok")
        return redirect(url_for("main.profil"))
    return _render_profile(me, own=True)


@bp.route("/profil/<int:mid>")
def profil_ansehen(mid):
    m = get_db().execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
    if not m:
        abort(404)
    return _render_profile(m, own=(mid == g.user.get("id")))


def _render_profile(m, own):
    from . import kstats
    db = get_db()
    roles, rights, vertretung = _rights_for(m["id"])
    res = kstats.Resolver(db)
    stats, _ = kstats.compute(kstats.load_matches("kicker", db=db, counted_only=True), res)
    tit = kstats.titles("kicker", db=db, resolver=res, counted_only=True).get(m["id"], {"gold": 0})
    nick = [r["alias"] for r in db.execute("SELECT alias FROM player_aliases WHERE member_id=? ORDER BY alias", (m["id"],))]
    acc = logic.member_account(m, g.settings) if (own or auth.can("kasse") or auth.can("vorstand")) else None
    pref = db.execute("SELECT * FROM notify_prefs WHERE member_id=?", (m["id"],)).fetchone() if own else None
    n_pens = db.execute("SELECT COUNT(*) FROM penalties WHERE member_id=? AND substr(date,1,4)=?",
                        (m["id"], str(date.today().year))).fetchone()[0]
    yes = db.execute("SELECT COUNT(*) FROM event_rsvp r JOIN events e ON e.id=r.event_id WHERE r.member_id=? AND r.status='ja' "
                     "AND substr(e.date,1,4)=?", (m["id"], str(date.today().year))).fetchone()[0]
    k = stats.get(m["id"])
    partner = (res.members[k["best_partner"]["id"]]["short"] if k and k.get("best_partner") else None)
    merch = db.execute("SELECT DISTINCT x.id, x.name, x.year, x.kind FROM merch x LEFT JOIN merch_owners o ON o.merch_id=x.id "
                       "WHERE o.member_id=? OR x.holder_id=? ORDER BY x.year DESC, x.name", (m["id"], m["id"])).fetchall()
    return render_template("profil.html", m=m, own=own, roles=sorted(roles), rights=rights, vertretung=vertretung,
                           partner=partner,
                           k=stats.get(m["id"]), titles=tit.get("gold", 0), nick=nick, acc=acc, pref=pref,
                           positions=POSITIONS, n_pens=n_pens, yes=yes, merch=merch)


# =============================================================================
# Impressum (öffentlich erreichbar, falls in Cloudflare freigegeben)
# =============================================================================

@bp.route("/impressum")
def impressum():
    from .db import get_settings
    s = get_settings()
    if not (s.get("impressum") or "").strip():
        abort(404)
    return render_template("impressum.html", text=s["impressum"], s=s)
