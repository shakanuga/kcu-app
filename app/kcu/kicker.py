"""Kicker: Statistik, Kickertool-Import/-Export und eigene Turniere in allen Kickertool-Modi mit TV-Ansicht."""
import json
import random
import secrets
import time
from collections import defaultdict
from datetime import date, datetime

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import auth, kmodes, kstats, ktimport
from .db import get_db, get_settings
from .views import actor, require

bp = Blueprint("kicker", __name__, url_prefix="/kicker")

LIVE_MODES = {
    "monster_dyp": "Monster-DYP",
    "whist": "Whist",
    "lms": "Mit Leben",
    "round_robin": "Jeder gegen jeden",
    "swiss": "Schweizer System",
    "ko": "K.-o.-System",
    "doko": "Doppel-K.-o.",
    "teams": "Feste Teams – jeder gegen jeden",
    "single": "Einzel – jeder gegen jeden",
}


@bp.app_template_global()
def mode_label(mode):
    return LIVE_MODES.get(mode) or ktimport.MODE_LABEL.get(mode, mode)


@bp.app_template_global()
def discipline_label(d):
    return kstats.DISCIPLINE_LABEL.get(d, d)


def _years(db, discipline="kicker"):
    return [r[0] for r in db.execute(
        "SELECT DISTINCT substr(date,1,4) y FROM kt_tournaments WHERE discipline=? ORDER BY y DESC", (discipline,))]


# =============================================================================
# Übersicht & Statistik
# =============================================================================

@bp.route("/")
def index():
    db = get_db()
    disc = request.args.get("d", "kicker")
    if disc not in kstats.DISCIPLINE_LABEL:
        disc = "kicker"
    year = request.args.get("jahr", "")
    year = year if year.isdigit() else ""
    alle = request.args.get("alle") == "1"
    res = kstats.Resolver(db)
    matches = kstats.load_matches(disc, year or None, db=db, counted_only=not alle)
    stats, records = kstats.compute(matches, res)
    tit = kstats.titles(disc, year or None, db=db, resolver=res, counted_only=not alle)
    min_games = max(3, min(20, len(matches) // 40)) if matches else 3
    board = []
    for mid, p in stats.items():
        m = res.members.get(mid)
        if not m:
            continue
        t = tit.get(mid, {})
        board.append({"m": m, "p": p, "gold": t.get("gold", 0), "silver": t.get("silver", 0), "bronze": t.get("bronze", 0)})
    sort = request.args.get("sort", "stars")
    keys = {
        "stars": lambda r: (-r["p"]["stars"], -r["p"]["win_pct"]),
        "pct": lambda r: (r["p"]["games"] < min_games, -r["p"]["win_pct"], -r["p"]["games"]),
        "titel": lambda r: (-r["gold"], -r["silver"], -r["bronze"], -r["p"]["stars"]),
        "tore": lambda r: (-r["p"]["goal_diff"], -r["p"]["gf"]),
        "spiele": lambda r: (-r["p"]["games"],),
    }
    board.sort(key=keys.get(sort, keys["stars"]))
    live = db.execute("SELECT * FROM kt_tournaments WHERE source='live' AND status='laeuft' ORDER BY created_at DESC").fetchall()
    recent = db.execute(
        "SELECT t.*, (SELECT COUNT(*) FROM kt_matches m WHERE m.tournament_id=t.id) n FROM kt_tournaments t "
        "WHERE discipline=? ORDER BY date DESC, id DESC LIMIT 8", (disc,)).fetchall()
    winners = _winners([r["id"] for r in recent], res, db)
    counting = kstats.counting_info(db)
    unknown = _unknown_names(db, res) if auth.can("kicker_verwalten") else []
    total_goals = sum(m["score1"] + m["score2"] for m in matches)
    return render_template("kicker/index.html", board=board, sort=sort, year=year, years=_years(db, disc), disc=disc,
                           live=live, recent=recent, winners=winners, n_matches=len(matches), total_goals=total_goals,
                           n_tournaments=len({m["tournament_id"] for m in matches}), min_games=min_games,
                           records=records, res=res, unknown_count=len(unknown), alle=alle, counting=counting)


def _winners(tids, res, db):
    out = {}
    if not tids:
        return out
    q = "SELECT * FROM kt_standings WHERE place=1 AND tournament_id IN (%s)" % ",".join("?" * len(tids))
    for r in db.execute(q, tids):
        names = json.loads(r["players"]) if r["players"] else [r["name"]]
        out.setdefault(r["tournament_id"], []).append(" & ".join(res.label(n) for n in names))
    return {k: " / ".join(v) for k, v in out.items()}


@bp.route("/spieler/<int:mid>")
def spieler(mid):
    db = get_db()
    res = kstats.Resolver(db)
    m = res.members.get(mid)
    if not m:
        abort(404)
    year = request.args.get("jahr", "")
    year = year if year.isdigit() else ""
    matches = kstats.load_matches("kicker", year or None, db=db, counted_only=True)
    stats, _ = kstats.compute(matches, res, min_pair=3)
    p = stats.get(mid)
    tit = kstats.titles("kicker", year or None, db=db, resolver=res, counted_only=True).get(mid, {"gold": 0, "silver": 0, "bronze": 0, "won": []})
    mine = [x for x in matches if mid in {res.member_id(n) for n in x["t1"] + x["t2"]}]
    per_year = defaultdict(lambda: {"games": 0, "wins": 0})
    for x in kstats.load_matches("kicker", None, db=db, counted_only=True):
        ids1 = {res.member_id(n) for n in x["t1"]}
        ids2 = {res.member_id(n) for n in x["t2"]}
        if mid in ids1 or mid in ids2:
            y = x["t_date"][:4]
            per_year[y]["games"] += 1
            won = (x["score1"] > x["score2"]) if mid in ids1 else (x["score2"] > x["score1"])
            per_year[y]["wins"] += 1 if won else 0
    aliases = [r["alias"] for r in db.execute("SELECT alias FROM player_aliases WHERE member_id=? ORDER BY alias", (mid,))]
    return render_template("kicker/spieler.html", m=m, p=p, tit=tit, last=list(reversed(mine[-15:])), res=res,
                           year=year, years=_years(db), per_year=sorted(per_year.items()), aliases=aliases)


@bp.route("/turniere")
def turniere():
    db = get_db()
    disc = request.args.get("d", "")
    q = ("SELECT t.*, (SELECT COUNT(*) FROM kt_matches m WHERE m.tournament_id=t.id AND m.status='fertig') n "
         "FROM kt_tournaments t")
    args = []
    if disc in kstats.DISCIPLINE_LABEL:
        q += " WHERE discipline=?"
        args.append(disc)
    q += " ORDER BY date DESC, id DESC"
    rows = db.execute(q, args).fetchall()
    res = kstats.Resolver(db)
    return render_template("kicker/turniere.html", rows=rows, disc=disc, winners=_winners([r["id"] for r in rows], res, db),
                           counting=kstats.counting_info(db))


@bp.route("/turnier/<int:tid>", methods=["GET", "POST"])
def turnier(tid):
    db = get_db()
    t = db.execute("SELECT * FROM kt_tournaments WHERE id=?", (tid,)).fetchone()
    if not t:
        abort(404)
    if t["source"] == "live" and t["status"] == "laeuft":
        return redirect(url_for("kicker.live", tid=tid))
    if request.method == "POST":
        if not auth.can("kicker_verwalten"):
            abort(403)
        if request.form.get("delete"):
            db.execute("DELETE FROM kt_tournaments WHERE id=?", (tid,))
            db.commit()
            flash(f"Turnier „{t['name']}“ gelöscht.", "ok")
            return redirect(url_for("kicker.turniere"))
        if "wertung" in request.form:
            val = {"auto": None, "ja": 1, "nein": 0}.get(request.form.get("wertung"), None)
            db.execute("UPDATE kt_tournaments SET counts=? WHERE id=?", (val, tid))
            db.commit()
            flash("Wertung gespeichert.", "ok")
            return redirect(url_for("kicker.turnier", tid=tid))
        disc = request.form.get("discipline")
        name = (request.form.get("name") or "").strip()[:100]
        d = request.form.get("date") or t["date"]
        if disc in kstats.DISCIPLINE_LABEL and name:
            db.execute("UPDATE kt_tournaments SET discipline=?, name=?, date=? WHERE id=?", (disc, name, d[:10], tid))
            db.commit()
            flash("Gespeichert.", "ok")
        return redirect(url_for("kicker.turnier", tid=tid))
    res = kstats.Resolver(db)
    matches = kstats.load_matches(None, None, tournament_id=tid, db=db)
    standings = db.execute("SELECT * FROM kt_standings WHERE tournament_id=? ORDER BY place IS NULL, place, name",
                           (tid,)).fetchall()
    st = []
    for s in standings:
        names = json.loads(s["players"]) if s["players"] else [s["name"]]
        st.append({"place": s["place"], "label": " & ".join(res.label(n) for n in names), "raw": s["name"],
                   "team": bool(s["players"])})
    rounds = defaultdict(list)
    for m in matches:
        rounds[(m["stage"], m["round_no"], m["round_label"])].append(m)
    def order(kv):
        stage, rno = kv[0][0], kv[0][1]
        return (0, rno, 0) if stage == "gruppe" else (1,) + kmodes.play_order(stage, rno)
    bracket = []
    if t["source"] == "live" and _opts(t).get("mode") in kmodes.BRACKET_MODES:
        bracket = _bracket_columns(t, _live_matches(db, tid))
    return render_template("kicker/turnier.html", t=t, rounds=sorted(rounds.items(), key=order),
                           standings=st, res=res, n=len(matches), bracket=bracket, bye=kmodes.BYE,
                           wertung=kstats.counting_info(db).get(tid, (False, "auto")))


# =============================================================================
# Import & Namenszuordnung
# =============================================================================

@bp.route("/import", methods=["GET", "POST"])
@require("kicker_verwalten")
def importieren():
    db = get_db()
    results = []
    if request.method == "POST":
        files = request.files.getlist("files")
        replace = bool(request.form.get("replace"))
        if not files or not any(f.filename for f in files):
            flash("Bitte mindestens eine Datei auswählen.", "error")
        entries = []   # (Dateiname, Inhalt) – ZIP-Archive werden ausgepackt
        for f in files:
            if not f.filename:
                continue
            raw = f.read(60 * 1024 * 1024)
            if f.filename.lower().endswith(".zip") or raw[:4] == b"PK\x03\x04":
                entries += _unzip(raw, f.filename, results)
            else:
                entries.append((f.filename, raw))
        # .ktool nach den JSON-Exporten, damit vorhandene Platzierungen erhalten bleiben
        entries.sort(key=lambda e: e[0].lower().endswith(".ktool"))
        for fname, raw in entries:
            try:
                t = ktimport.parse_file(raw, fname)
            except ktimport.ImportError_ as e:
                results.append({"file": fname, "state": "fehler", "msg": str(e)})
                continue
            if not t["matches"]:
                results.append({"file": fname, "state": "leer", "msg": f"„{t['name']}“ hat keine gespielten Spiele."})
                continue
            tid, state = ktimport.save_tournament(db, t, actor(), replace=replace)
            _ensure_standings(db, tid)
            results.append({"file": fname, "state": state, "tid": tid, "name": t["name"], "date": t["date"],
                            "n": len(t["matches"]), "discipline": db.execute(
                                "SELECT discipline FROM kt_tournaments WHERE id=?", (tid,)).fetchone()[0]})
        db.commit()
        n_new = sum(1 for r in results if r["state"] == "neu")
        n_upd = sum(1 for r in results if r["state"] == "aktualisiert")
        if results:
            flash(f"{n_new} Turnier(e) neu, {n_upd} aktualisiert.", "ok")
    res = kstats.Resolver(db)
    return render_template("kicker/import.html", results=results, unknown=_unknown_names(db, res)[:12],
                           n_total=db.execute("SELECT COUNT(*) FROM kt_tournaments").fetchone()[0])


def _unzip(raw, name, results, max_files=500, max_total=200 * 1024 * 1024):
    import io
    import os
    import zipfile
    out, total = [], 0
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        results.append({"file": name, "state": "fehler", "msg": f"{name}: ZIP-Datei ist beschädigt."})
        return out
    for info in z.infolist()[:max_files]:
        base = os.path.basename(info.filename)
        if info.is_dir() or base.startswith((".", "__MACOSX")) or "__MACOSX" in info.filename:
            continue
        if not base.lower().endswith((".json", ".ktool")):
            continue
        total += info.file_size
        if info.file_size > 20 * 1024 * 1024 or total > max_total:
            results.append({"file": base, "state": "fehler", "msg": f"{base}: zu groß."})
            continue
        out.append((base, z.read(info)))
    return out


def _ensure_standings(db, tid):
    """Turniere ohne Platzierungen (z. B. .ktool-Dateien): Sieger nach Siegen/Tordifferenz bestimmen."""
    if db.execute("SELECT 1 FROM kt_standings WHERE tournament_id=? AND place IS NOT NULL", (tid,)).fetchone():
        return
    matches = kstats.load_matches(None, None, tournament_id=tid, db=db)
    tab = defaultdict(lambda: [0, 0])
    for m in matches:
        for side, gf, ga in ((m["t1"], m["score1"], m["score2"]), (m["t2"], m["score2"], m["score1"])):
            for n in side:
                tab[n][0] += 1 if gf > ga else 0
                tab[n][1] += gf - ga
    ranked = sorted(tab.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))
    db.execute("DELETE FROM kt_standings WHERE tournament_id=?", (tid,))
    place, prev = 0, None
    for i, (n, v) in enumerate(ranked, 1):
        if tuple(v) != prev:
            place = i
        prev = tuple(v)
        db.execute("INSERT OR REPLACE INTO kt_standings(tournament_id, name, place) VALUES (?,?,?)", (tid, n, place))


def _unknown_names(db, res):
    counts = defaultdict(int)
    last = {}
    for r in db.execute("SELECT m.team1, m.team2, t.date FROM kt_matches m JOIN kt_tournaments t ON t.id=m.tournament_id "
                        "WHERE t.discipline='kicker'"):
        for n in json.loads(r["team1"]) + json.loads(r["team2"]):
            if not res.known(n):
                counts[n] += 1
                last[n] = max(last.get(n, ""), r["date"])
    return sorted(({"name": n, "count": c, "last": last[n]} for n, c in counts.items()), key=lambda x: -x["count"])


@bp.route("/namen", methods=["GET", "POST"])
@require("kicker_verwalten")
def namen():
    db = get_db()
    if request.method == "POST":
        n = 0
        for key, val in request.form.items():
            if not key.startswith("map__"):
                continue
            alias = key[5:].strip().lower()[:80]
            if not alias:
                continue
            if val == "":
                continue
            if val == "gast":
                db.execute("INSERT INTO player_aliases(alias, member_id, ignore) VALUES (?, NULL, 1) "
                           "ON CONFLICT(alias) DO UPDATE SET member_id=NULL, ignore=1", (alias,))
            elif val == "loeschen":
                db.execute("DELETE FROM player_aliases WHERE alias=?", (alias,))
            elif val.isdigit():
                db.execute("INSERT INTO player_aliases(alias, member_id, ignore) VALUES (?, ?, 0) "
                           "ON CONFLICT(alias) DO UPDATE SET member_id=excluded.member_id, ignore=0", (alias, int(val)))
            else:
                continue
            n += 1
        db.commit()
        flash(f"{n} Zuordnung(en) gespeichert. Die Statistik rechnet ab sofort damit.", "ok")
        return redirect(url_for("kicker.namen"))
    res = kstats.Resolver(db)
    members = db.execute("SELECT id, name, short, status FROM members ORDER BY CASE status WHEN 'aktiv' THEN 0 "
                         "WHEN 'ehren' THEN 1 ELSE 2 END, sort, name").fetchall()
    aliases = db.execute("SELECT a.*, m.short, m.name FROM player_aliases a LEFT JOIN members m ON m.id=a.member_id "
                         "ORDER BY a.ignore, m.sort, a.alias").fetchall()
    return render_template("kicker/namen.html", unknown=_unknown_names(db, res), members=members, aliases=aliases)


# =============================================================================
# Eigene Turniere – alle Modi wie im Kickertool
# =============================================================================

ROTATING = ("monster_dyp", "whist", "lms")          # jede Runde neue 2er-Teams, Einzelwertung
ENTRY_MODES = ("round_robin", "swiss", "ko", "doko")  # feste Teilnehmer (Einzel oder Teams)

MODE_INFO = {
    "monster_dyp": ("🔀", "Jede Runde neue Partner und Gegner, Einzelwertung. Euer Standard."),
    "whist": ("🤝", "Wie Monster-DYP, aber so ausgelost, dass jeder möglichst genau einmal mit jedem zusammenspielt."),
    "lms": ("❤️", "Jede Runde neu gemischt. Wer verliert, verliert ein Leben – ohne Leben fliegt man raus."),
    "round_robin": ("🔁", "Jeder gegen jeden – als Einzel oder mit festen Teams (z. B. Sommerfeier)."),
    "swiss": ("🇨🇭", "Gleich starke Gegner treffen aufeinander, feste Rundenzahl, kein Ausscheiden."),
    "ko": ("🏆", "Turnierbaum: Wer verliert, ist raus. Optional mit Spiel um Platz 3."),
    "doko": ("🪜", "Turnierbaum mit zweiter Chance: Erst nach der zweiten Niederlage ist man raus."),
}


def _opts(t):
    try:
        o = json.loads(t["options"] or "{}")
    except (TypeError, ValueError):
        o = {}
    # alte Turniere (v2.1)
    if o.get("mode") == "teams":
        o.update(mode="round_robin", entry="team")
    elif o.get("mode") == "single":
        o.update(mode="round_robin", entry="einzel")
    return o


def _participants(db, tid):
    return [dict(r) for r in db.execute("SELECT * FROM kt_participants WHERE tournament_id=? ORDER BY sort, name", (tid,))]


def _live_matches(db, tid):
    rows = []
    for r in db.execute("SELECT * FROM kt_matches WHERE tournament_id=? ORDER BY round_no, seq", (tid,)):
        d = dict(r)
        d["t1"] = json.loads(r["team1"])
        d["t2"] = json.loads(r["team2"])
        rows.append(d)
    return rows


def _entries(parts):
    """{schlüssel: spielerliste} – Teamname bei Teams, sonst Spielername."""
    out = {}
    for p in parts:
        key = p["team"] or p["name"]
        out.setdefault(key, []).append(p["name"])
    return out


def _key_fn(parts):
    team_of = {p["name"]: (p["team"] or p["name"]) for p in parts}

    def key(side):
        if not side or kmodes.is_bye(side):
            return None
        return team_of.get(side[0], side[0])
    return key


def _lms_state(parts, matches, lives_start):
    """Leben dynamisch aus den Ergebnissen berechnen (Korrekturen wirken sofort)."""
    lives = {p["name"]: lives_start for p in parts}
    out_round = {}
    for m in matches:
        if m["status"] != "fertig" or m["score1"] is None:
            continue
        losers = m["t2"] if m["score1"] > m["score2"] else m["t1"]
        for n in losers:
            if n in lives and lives[n] > 0:
                lives[n] -= 1
                if lives[n] == 0:
                    out_round[n] = m["round_no"]
    return lives, out_round


def _table(t, parts, matches):
    o = _opts(t)
    mode = o.get("mode")
    if mode in ROTATING:
        lives, out_round = (_lms_state(parts, matches, int(o.get("lives", 2))) if mode == "lms" else ({}, {}))
        plist = [{"name": p["name"], "key": p["name"], "label": p["name"], "members": [p["name"]],
                  "lives": lives.get(p["name"]) if mode == "lms" else None,
                  "out_round": out_round.get(p["name"])} for p in parts]
        for m in matches:
            m["k1"], m["k2"] = m["t1"], m["t2"]
    else:
        key = _key_fn(parts)
        plist = [{"name": k, "key": k, "label": k, "members": v} for k, v in _entries(parts).items()]
        for m in matches:
            k1, k2 = key(m["t1"]), key(m["t2"])
            m["k1"], m["k2"] = ([k1] if k1 else []), ([k2] if k2 else [])
    rows = kstats.standings_live(matches, plist, mode)
    if mode in kmodes.BRACKET_MODES and o.get("size") and _bracket_done(t, matches):
        pl = kmodes.placements(mode, o["size"], matches)
        by_members = {frozenset(v): k for k, v in _entries(parts).items()}
        place_of = {by_members[frozenset(t_)]: p for t_, p in pl.items() if frozenset(t_) in by_members}
        for r in rows:
            r["ko_place"] = place_of.get(r["name"])
        rows.sort(key=lambda r: (r["ko_place"] or 999, -r["wins"], -r["diff"]))
        for r in rows:
            r["place"] = r["ko_place"] or "–"
    elif mode in kmodes.BRACKET_MODES:
        # läuft noch: wer noch im Rennen ist, steht oben
        limit = 2 if mode == "doko" else 1
        rows.sort(key=lambda r: (r["losses"] >= limit, -r["wins"], r["losses"], -r["diff"]))
        for i, r in enumerate(rows, 1):
            r["place"] = i
    return rows


def _pair_score(groups, partner_cnt, opp_cnt, partner_weight=10):
    s = 0
    for a, b in groups:
        for side in (a, b):
            if len(side) == 2:
                s += partner_weight * partner_cnt[frozenset(side)]
        for x in a:
            for y in b:
                s += opp_cnt[frozenset((x, y))]
    return s


def _next_round(db, t, parts, matches):
    """Neue Runde für Monster-DYP, Whist bzw. Mit Leben auslosen. Rückgabe: Anzahl neuer Spiele."""
    o = _opts(t)
    mode = o.get("mode")
    if mode == "swiss":
        return _swiss_round(db, t, parts, matches)
    if mode not in ROTATING:
        return 0
    if mode == "lms":
        lives, _ = _lms_state(parts, matches, int(o.get("lives", 2)))
        active = [p["name"] for p in parts if lives.get(p["name"], 0) > 0]
    else:
        active = [p["name"] for p in parts]
    if len(active) < 2:
        return 0
    size = 2 if len(active) >= 4 else 1
    per_match = 2 * size
    n_matches = len(active) // per_match
    played = defaultdict(int)
    byes = defaultdict(int)
    rounds_done = max((m["round_no"] for m in matches), default=0)
    for m in matches:
        for n in m["t1"] + m["t2"]:
            played[n] += 1
    for n in active:
        byes[n] = rounds_done - len({m["round_no"] for m in matches if n in m["t1"] + m["t2"]})
    n_bye = len(active) - n_matches * per_match
    pool = sorted(active, key=lambda n: (-byes[n], played[n], random.random()))
    playing = pool[:len(active) - n_bye] if n_bye else pool
    partner_cnt = defaultdict(int)
    opp_cnt = defaultdict(int)
    for m in matches:
        for side in (m["t1"], m["t2"]):
            if len(side) == 2:
                partner_cnt[frozenset(side)] += 1
        for x in m["t1"]:
            for y in m["t2"]:
                opp_cnt[frozenset((x, y))] += 1
    weight = 100 if mode == "whist" else 10
    best, best_s = None, None
    for _ in range(1500 if mode == "whist" else 400):
        random.shuffle(playing)
        groups = []
        for i in range(n_matches):
            chunk = playing[i * per_match:(i + 1) * per_match]
            groups.append((chunk[:size], chunk[size:]))
        sc = _pair_score(groups, partner_cnt, opp_cnt, weight)
        if best is None or sc < best_s:
            best, best_s = [(list(a), list(b)) for a, b in groups], sc
            if sc == 0:
                break
    rno = rounds_done + 1
    seq = max((m["seq"] for m in matches), default=0)
    for a, b in best:
        seq += 1
        db.execute("INSERT INTO kt_matches(tournament_id, stage, round_no, round_label, seq, team1, team2, status) "
                   "VALUES (?, 'gruppe', ?, ?, ?, ?, ?, 'offen')",
                   (t["id"], rno, f"Runde {rno}", seq, kmodes.dumps(a), kmodes.dumps(b)))
    return len(best)


def _swiss_round(db, t, parts, matches):
    entries = _entries(parts)
    rno = max((m["round_no"] for m in matches), default=0) + 1
    pairs = kmodes.swiss_pairs(entries, matches, _key_fn(parts))
    seq = max((m["seq"] for m in matches), default=0)
    for a, b in pairs:
        seq += 1
        if b is None:
            db.execute("INSERT INTO kt_matches(tournament_id, stage, round_no, round_label, seq, team1, team2, status) "
                       "VALUES (?, 'gruppe', ?, ?, ?, ?, ?, 'freilos')",
                       (t["id"], rno, f"Runde {rno}", seq, kmodes.dumps(entries[a]), kmodes.dumps([kmodes.BYE])))
        else:
            db.execute("INSERT INTO kt_matches(tournament_id, stage, round_no, round_label, seq, team1, team2, status) "
                       "VALUES (?, 'gruppe', ?, ?, ?, ?, ?, 'offen')",
                       (t["id"], rno, f"Runde {rno}", seq, kmodes.dumps(entries[a]), kmodes.dumps(entries[b])))
    return len(pairs)


def _round_robin(entries):
    """Kreis-Methode: Liste von Runden mit (a, b)-Paaren."""
    e = list(entries)
    if len(e) % 2:
        e.append(None)
    n = len(e)
    rounds = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = e[i], e[n - 1 - i]
            if a is not None and b is not None:
                pairs.append((a, b) if r % 2 == 0 else (b, a))
        rounds.append(pairs)
        e = [e[0]] + [e[-1]] + e[1:-1]
    return rounds


# --- Turnierbaum: Sieger/Verlierer weiterreichen, Freilose auflösen -----------------

def _bracket_match(db, tid, stage, r, i):
    return db.execute("SELECT * FROM kt_matches WHERE tournament_id=? AND stage=? AND round_no=? AND seq=?",
                      (tid, stage, r, i)).fetchone()


def _place(db, t, target, side):
    if not target:
        return
    stage, r, i, slot = target
    m = _bracket_match(db, t["id"], stage, r, i)
    if not m:
        return
    col = "team1" if slot == 0 else "team2"
    db.execute(f"UPDATE kt_matches SET {col}=? WHERE id=?", (kmodes.dumps(side), m["id"]))


def _resolve_bracket(db, t):
    """Spiele mit beiden Teilnehmern freigeben bzw. Freilose automatisch weiterreichen."""
    o = _opts(t)
    S = o.get("size")
    for _ in range(64):
        changed = False
        for m in _live_matches(db, t["id"]):
            if m["status"] != "wartet" or not m["t1"] or not m["t2"]:
                continue
            if kmodes.is_bye(m["t1"]) or kmodes.is_bye(m["t2"]):
                db.execute("UPDATE kt_matches SET status='freilos' WHERE id=?", (m["id"],))
                m["status"] = "freilos"
                w, l = kmodes.winner_loser(m)
                tw, tl = kmodes.targets(o["mode"], S, m["stage"], m["round_no"], m["seq"])
                _place(db, t, tw, w)
                _place(db, t, tl, l)
            else:
                db.execute("UPDATE kt_matches SET status='offen' WHERE id=?", (m["id"],))
            changed = True
        if not changed:
            break


def _bracket_done(t, matches):
    o = _opts(t)
    if o["mode"] == "doko":
        gf = next((m for m in matches if m["stage"] == "gf"), None)
        return bool(gf and gf["status"] in ("fertig", "freilos"))
    return all(m["status"] in ("fertig", "freilos") for m in matches)


def _finish(db, t):
    parts = _participants(db, t["id"])
    matches = _live_matches(db, t["id"])
    o = _opts(t)
    table = _table(t, parts, matches)
    db.execute("DELETE FROM kt_matches WHERE tournament_id=? AND status NOT IN ('fertig')", (t["id"],))
    db.execute("DELETE FROM kt_standings WHERE tournament_id=?", (t["id"],))
    team_based = o.get("entry") == "team"
    for r in table:
        place = r["place"] if isinstance(r["place"], int) else None
        db.execute("INSERT INTO kt_standings(tournament_id, name, players, place) VALUES (?,?,?,?)",
                   (t["id"], r["name"], kmodes.dumps(r["members"]) if team_based else None, place))
    db.execute("UPDATE kt_tournaments SET status='fertig' WHERE id=?", (t["id"],))


def _strength(db):
    """Sterne je Spielername (für Setzliste und Snake-Teams)."""
    res = kstats.Resolver(db)
    stats, _ = kstats.compute(kstats.load_matches("kicker", db=db, counted_only=True), res)
    return res, stats


@bp.route("/neu", methods=["GET", "POST"])
def neu():
    db = get_db()
    if not g.user.get("id") and not auth.can("kicker_verwalten"):
        abort(403)
    members = db.execute("SELECT * FROM members WHERE status IN ('aktiv','ehren') ORDER BY "
                         "CASE status WHEN 'aktiv' THEN 0 ELSE 1 END, sort, name").fetchall()
    ctx = {"members": members, "modes": LIVE_MODES, "info": MODE_INFO}
    if request.method == "POST":
        f = request.form
        mode = f.get("mode")
        if mode not in MODE_INFO:
            abort(400)
        entry = "team" if f.get("entry") == "team" else "einzel"
        names = []
        by_id = {m["id"]: (m["short"] or m["name"]) for m in members}
        for mid in f.getlist("member_ids", type=int):
            if mid in by_id:
                names.append(by_id[mid])
        for guest in (f.get("guests") or "").replace(";", ",").replace("\n", ",").split(","):
            guest = " ".join(guest.split())[:30]
            if guest and guest.lower() not in {n.lower() for n in names}:
                names.append(guest)
        ziel = max(1, min(99, f.get("ziel", type=int) or 10))
        lives = max(1, min(9, f.get("lives", type=int) or 2))
        rounds_planned = f.get("rounds", type=int) or None
        need = 4 if (mode in ROTATING or entry == "team") else 2
        if mode == "doko":
            need = max(need, 4 if entry == "einzel" else 8)
        if len(names) < need:
            flash(f"Für diesen Modus braucht ihr mindestens {need} Spieler.", "error")
            return render_template("kicker/neu.html", form=f, **ctx)
        if mode == "whist" and not rounds_planned:
            rounds_planned = len(names) - 1 if len(names) % 2 == 0 else len(names)
        title = (f.get("name") or "").strip()[:80] or f"{LIVE_MODES[mode]} {date.today().strftime('%d.%m.%Y')}"
        try:
            tmin = float((f.get("timer") or "").replace(",", ".") or 0)
        except ValueError:
            tmin = 0
        opts = {"mode": mode, "ziel": ziel, "lives": lives, "rounds_planned": rounds_planned, "entry": entry,
                "third": bool(f.get("third")), "timer_dur": int(max(0, min(60, tmin)) * 60)}
        res, stats = _strength(db)
        strength = {n: (stats.get(res.member_id(n)) or {}).get("stars", 0) for n in names}
        # Teilnehmer festlegen
        if mode in ROTATING:
            random.shuffle(names)
            entries = [[n] for n in names]
            opts["entry"] = "einzel"
        elif entry == "team":
            entries = kmodes.build_teams(names, f.get("teambuild", "zufall"), f.get("teams_manual", ""), strength)
            if len(entries) < 2:
                flash("Für Teams braucht ihr mindestens zwei Teams.", "error")
                return render_template("kicker/neu.html", form=f, **ctx)
        else:
            entries = [[n] for n in names]
            random.shuffle(entries)
        if mode in kmodes.BRACKET_MODES and f.get("seeding") == "staerke":
            entries.sort(key=lambda e: -sum(strength.get(n, 0) for n in e))
        if mode == "swiss" and not rounds_planned:
            opts["rounds_planned"] = kmodes.swiss_rounds_default(len(entries))
        name_type = "monster_dyp" if mode in ROTATING else ("team" if opts["entry"] == "team" else "single")
        tid = db.execute("INSERT INTO kt_tournaments(name, date, mode, name_type, discipline, source, status, options, created_by) "
                         "VALUES (?,?,?,?,?, 'live', 'laeuft', ?, ?)",
                         (title, date.today().isoformat(), mode, name_type,
                          f.get("discipline") if f.get("discipline") in kstats.DISCIPLINE_LABEL else "kicker",
                          json.dumps(opts), actor())).lastrowid
        for i, tm in enumerate(entries):
            label = " & ".join(tm) if opts["entry"] == "team" else None
            for n in tm:
                db.execute("INSERT OR IGNORE INTO kt_participants(tournament_id, name, team, sort) VALUES (?,?,?,?)",
                           (tid, n, label, i))
        t = db.execute("SELECT * FROM kt_tournaments WHERE id=?", (tid,)).fetchone()
        if mode in ROTATING or mode == "swiss":
            _next_round(db, t, _participants(db, tid), [])
        elif mode == "round_robin":
            seq = 0
            for rno, pairs in enumerate(_round_robin(entries), 1):
                for a, b in pairs:
                    seq += 1
                    db.execute("INSERT INTO kt_matches(tournament_id, stage, round_no, round_label, seq, team1, team2, status) "
                               "VALUES (?, 'gruppe', ?, ?, ?, ?, ?, 'offen')",
                               (tid, rno, f"Runde {rno}", seq, kmodes.dumps(a), kmodes.dumps(b)))
        else:
            matches, size = kmodes.create_bracket(entries, mode, opts["third"])
            opts["size"] = size
            db.execute("UPDATE kt_tournaments SET options=? WHERE id=?", (json.dumps(opts), tid))
            for m in matches:
                db.execute("INSERT INTO kt_matches(tournament_id, stage, round_no, round_label, seq, team1, team2, status) "
                           "VALUES (?,?,?,?,?,?,?, 'wartet')",
                           (tid, m["stage"], m["round_no"], m["label"], m["seq"], kmodes.dumps(m["t1"]), kmodes.dumps(m["t2"])))
            t = db.execute("SELECT * FROM kt_tournaments WHERE id=?", (tid,)).fetchone()
            _resolve_bracket(db, t)
        db.commit()
        flash("Turnier gestartet – viel Erfolg! Die TV-Ansicht öffnest du oben rechts.", "ok")
        return redirect(url_for("kicker.live", tid=tid))
    return render_template("kicker/neu.html", form={}, **ctx)


def _sorted_open(t, matches):
    o = _opts(t)
    open_ = [m for m in matches if m["status"] == "offen"]
    if o.get("mode") in kmodes.BRACKET_MODES:
        open_.sort(key=lambda m: (kmodes.play_order(m["stage"], m["round_no"]), m["seq"]))
    return open_


def _bracket_columns(t, matches):
    """Für die Baum-Ansicht: Liste von (Titel, [Spiele]) je Stufe/Runde."""
    o = _opts(t)
    if o.get("mode") not in kmodes.BRACKET_MODES:
        return []
    groups = defaultdict(list)
    for m in matches:
        groups[(m["stage"], m["round_no"])].append(m)
    order = {"ko": 0, "wb": 0, "ko3": 1, "lb": 2, "gf": 3}
    cols = []
    for (stage, r), ms in sorted(groups.items(), key=lambda kv: (order.get(kv[0][0], 9), kv[0][1])):
        ms.sort(key=lambda m: m["seq"])
        cols.append({"stage": stage, "title": ms[0]["round_label"], "matches": ms})
    return cols


def _tv_columns(cols, keep=4):
    """Für den Fernseher nur die spannenden Runden: alles noch Offene plus die zuletzt fertige je Stufe."""
    if len(cols) <= keep:
        return cols
    out = []
    for stage in dict.fromkeys(c["stage"] for c in cols):
        sc = [c for c in cols if c["stage"] == stage]
        first_open = next((i for i, c in enumerate(sc) if any(m["status"] in ("offen", "wartet") for m in c["matches"])), None)
        if first_open is None:
            out.append(sc[-1])
        else:
            out.extend(sc[max(0, first_open - 1):])
    return out


def _live_context(tid):
    db = get_db()
    t = db.execute("SELECT * FROM kt_tournaments WHERE id=? AND source='live'", (tid,)).fetchone()
    if not t:
        abort(404)
    o = _opts(t)
    parts = _participants(db, tid)
    matches = _live_matches(db, tid)
    table = _table(t, parts, matches)
    open_ = _sorted_open(t, matches)
    current = open_[0] if open_ else None
    upcoming = open_[1:5]
    done = sorted((m for m in matches if m["status"] == "fertig"), key=lambda m: m["played_at"] or "")
    res = kstats.Resolver(db)
    winner = None
    if o.get("mode") == "lms":
        alive = [r for r in table if (r["lives"] or 0) > 0]
        if len(alive) == 1 and not open_:
            winner = alive[0]
    elif t["status"] == "fertig" and table:
        winner = table[0]
    playable = [m for m in matches if m["status"] != "freilos"]
    bracket = _bracket_columns(t, matches)
    return {"t": t, "o": o, "parts": parts, "matches": matches, "table": table, "current": current,
            "upcoming": upcoming, "done": list(reversed(done))[:12], "winner": winner, "res": res,
            "bracket": bracket, "tv_bracket": _tv_columns(bracket), "bye": kmodes.BYE,
            "round_no": max((m["round_no"] for m in matches), default=0),
            "n_done": len(done), "n_total": len(playable), "timer": _timer(t), "guest_token": None}


@bp.route("/live/<int:tid>")
def live(tid):
    ctx = _live_context(tid)
    if ctx["t"]["status"] == "fertig":
        return redirect(url_for("kicker.turnier", tid=tid))
    db = get_db()
    ctx["cat_kasten"] = db.execute("SELECT * FROM catalog WHERE code='02' AND active=1").fetchone()
    ctx["cat_ball"] = db.execute("SELECT * FROM catalog WHERE code='01' AND active=1").fetchone()
    return render_template("kicker/live.html", **ctx)


@bp.route("/live/<int:tid>/tv")
def tv(tid):
    ctx = _live_context(tid)
    if request.args.get("partial"):
        return render_template("kicker/_tv_inner.html", **ctx)
    return render_template("kicker/tv.html", **ctx)


@bp.route("/live/<int:tid>/spiel/<int:mid>", methods=["POST"])
def ergebnis(tid, mid):
    db = get_db()
    t = db.execute("SELECT * FROM kt_tournaments WHERE id=? AND source='live'", (tid,)).fetchone()
    m = db.execute("SELECT * FROM kt_matches WHERE id=? AND tournament_id=?", (mid, tid)).fetchone()
    if not t or not m:
        abort(404)
    if t["status"] != "laeuft":
        abort(400, "Das Turnier ist beendet.")
    o = _opts(t)
    bracket = o.get("mode") in kmodes.BRACKET_MODES
    if request.form.get("reset"):
        if bracket and m["status"] == "fertig":
            md = dict(m)
            md["t1"], md["t2"] = json.loads(m["team1"]), json.loads(m["team2"])
            for target in kmodes.targets(o["mode"], o["size"], m["stage"], m["round_no"], m["seq"]):
                if not target:
                    continue
                nxt = _bracket_match(db, tid, *target[:3])
                if nxt and nxt["status"] in ("fertig", "freilos"):
                    flash(f"Erst das Folgespiel ({nxt['round_label']}) zurücksetzen.", "error")
                    return redirect(url_for("kicker.live", tid=tid))
            for target in kmodes.targets(o["mode"], o["size"], m["stage"], m["round_no"], m["seq"]):
                if target:
                    _place(db, t, target, [])
                    nxt = _bracket_match(db, tid, *target[:3])
                    if nxt:
                        db.execute("UPDATE kt_matches SET status='wartet' WHERE id=?", (nxt["id"],))
        db.execute("UPDATE kt_matches SET score1=NULL, score2=NULL, status='offen', played_at=NULL WHERE id=?", (mid,))
        db.commit()
        return redirect(url_for("kicker.live", tid=tid))
    if m["status"] != "offen":
        flash("Dieses Spiel ist (noch) nicht spielbar.", "error")
        return redirect(url_for("kicker.live", tid=tid))
    s1 = request.form.get("score1", type=int)
    s2 = request.form.get("score2", type=int)
    if s1 is None or s2 is None or not (0 <= s1 <= 99 and 0 <= s2 <= 99) or s1 == s2:
        flash("Bitte ein gültiges Ergebnis eintragen (kein Unentschieden).", "error")
        return redirect(url_for("kicker.live", tid=tid))
    db.execute("UPDATE kt_matches SET score1=?, score2=?, status='fertig', played_at=? WHERE id=?",
               (s1, s2, datetime.now().strftime("%Y-%m-%d %H:%M"), mid))
    _reset_timer(db, t)
    parts = _participants(db, tid)
    finished = False
    if bracket:
        md = dict(m)
        md.update(t1=json.loads(m["team1"]), t2=json.loads(m["team2"]), score1=s1, score2=s2, status="fertig")
        w, l = kmodes.winner_loser(md)
        tw, tl = kmodes.targets(o["mode"], o["size"], m["stage"], m["round_no"], m["seq"])
        _place(db, t, tw, w)
        _place(db, t, tl, l)
        _resolve_bracket(db, t)
        finished = _bracket_done(t, _live_matches(db, tid))
    else:
        matches = _live_matches(db, tid)
        round_done = all(x["status"] in ("fertig", "freilos") for x in matches)
        rounds_done = max((x["round_no"] for x in matches), default=0)
        planned = o.get("rounds_planned")
        if o.get("mode") == "lms" and round_done:
            lives, _ = _lms_state(parts, matches, int(o.get("lives", 2)))
            if sum(1 for v in lives.values() if v > 0) <= 1:
                finished = True
            else:
                _next_round(db, t, parts, matches)
        elif o.get("mode") in ("monster_dyp", "whist", "swiss") and round_done:
            if not planned or rounds_done < planned:
                _next_round(db, t, parts, matches)
            else:
                finished = True
        elif o.get("mode") == "round_robin" and round_done:
            finished = True
    if finished:
        _finish(db, t)
        db.commit()
        flash("Turnier entschieden! 🏆", "ok")
        return redirect(url_for("kicker.turnier", tid=tid))
    db.commit()
    if max(s1, s2) >= int(o.get("ziel", 10)) and min(s1, s2) == 0:
        flash("Zu-Null! Die Kasten-Strafe kannst du unten direkt eintragen.", "ok")
    return redirect(url_for("kicker.live", tid=tid))


@bp.route("/live/<int:tid>/aktion", methods=["POST"])
def aktion(tid):
    db = get_db()
    t = db.execute("SELECT * FROM kt_tournaments WHERE id=? AND source='live'", (tid,)).fetchone()
    if not t:
        abort(404)
    a = request.form.get("a")
    if a == "runde":
        n = _next_round(db, t, _participants(db, tid), _live_matches(db, tid))
        flash(f"Neue Runde mit {n} Spiel(en) ausgelost." if n else "Keine weitere Runde möglich.", "ok" if n else "error")
    elif a == "beenden":
        _finish(db, t)
        db.commit()
        flash("Turnier beendet und in die Statistik übernommen.", "ok")
        return redirect(url_for("kicker.turnier", tid=tid))
    elif a == "loeschen":
        if not (auth.can("kicker_verwalten") or t["created_by"] == actor()):
            abort(403)
        db.execute("DELETE FROM kt_tournaments WHERE id=?", (tid,))
        db.commit()
        flash("Turnier gelöscht.", "ok")
        return redirect(url_for("kicker.index"))
    elif a == "offene_loeschen" and _opts(t).get("mode") in ROTATING:
        db.execute("DELETE FROM kt_matches WHERE tournament_id=? AND status<>'fertig'", (tid,))
        flash("Offene Spiele entfernt.", "ok")
    db.commit()
    return redirect(url_for("kicker.live", tid=tid))


def _status(tid):
    db = get_db()
    r = db.execute("SELECT COUNT(*) n, MAX(COALESCE(played_at,'')) last, SUM(status='fertig') done FROM kt_matches "
                   "WHERE tournament_id=?", (tid,)).fetchone()
    t = db.execute("SELECT * FROM kt_tournaments WHERE id=?", (tid,)).fetchone()
    if not t:
        abort(404)
    resp = jsonify({"v": f"{r['n']}-{r['done']}-{r['last']}-{t['status']}", "timer": _timer(t),
                    "now": int(time.time() * 1000)})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/live/<int:tid>/status.json")
def status_json(tid):
    return _status(tid)


# --- Spielzeit-Timer ---------------------------------------------------------------
# Zustand in den Turnier-Optionen: dur (Sekunden), ends (Zeitpunkt in ms, wenn er läuft), left (Sekunden, wenn pausiert)

def _timer(t):
    o = _opts(t)
    tm = o.get("timer") or {}
    dur = int(tm.get("dur") or o.get("timer_dur") or 0)
    if not dur:
        try:
            dur = int(float(get_settings().get("kicker_timer") or 0) * 60)
        except ValueError:
            dur = 0
    return {"dur": dur, "ends": tm.get("ends"), "left": tm.get("left")}


@bp.route("/live/<int:tid>/timer", methods=["POST"])
def timer(tid):
    db = get_db()
    t = db.execute("SELECT * FROM kt_tournaments WHERE id=? AND source='live'", (tid,)).fetchone()
    if not t:
        abort(404)
    o = _opts(t)
    tm = _timer(t)
    a = request.form.get("a")
    now = int(time.time() * 1000)
    if request.form.get("dur", type=int):
        tm["dur"] = max(10, min(3600, request.form.get("dur", type=int)))
        o["timer_dur"] = tm["dur"]
        if not tm["ends"]:
            tm["left"] = None
    if a == "start":
        left = tm["left"] if tm["left"] is not None else tm["dur"]
        tm.update(ends=now + int(left * 1000), left=None)
    elif a == "pause" and tm["ends"]:
        tm.update(left=max(0, round((tm["ends"] - now) / 1000)), ends=None)
    elif a == "reset":
        tm.update(ends=None, left=None)
    elif a == "plus":
        if tm["ends"]:
            tm["ends"] += 30000
        else:
            tm["left"] = (tm["left"] if tm["left"] is not None else tm["dur"]) + 30
    o["timer"] = tm
    db.execute("UPDATE kt_tournaments SET options=? WHERE id=?", (json.dumps(o), tid))
    db.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"timer": tm, "now": now})
    return redirect(url_for("kicker.live", tid=tid))


def _reset_timer(db, t):
    o = _opts(t)
    if o.get("timer"):
        o["timer"].update(ends=None, left=None)
        db.execute("UPDATE kt_tournaments SET options=? WHERE id=?", (json.dumps(o), t["id"]))


# --- Gast-Link: TV-Ansicht ohne Anmeldung (z. B. für den Fernseher) --------------------

@bp.route("/live/<int:tid>/gastlink", methods=["POST"])
def gastlink(tid):
    db = get_db()
    t = db.execute("SELECT * FROM kt_tournaments WHERE id=? AND source='live'", (tid,)).fetchone()
    if not t:
        abort(404)
    token = None if request.form.get("a") == "aus" else secrets.token_urlsafe(12)
    db.execute("UPDATE kt_tournaments SET guest_token=? WHERE id=?", (token, tid))
    db.commit()
    flash("Gast-Link erstellt." if token else "Gast-Link abgeschaltet.", "ok")
    return redirect(url_for("kicker.live", tid=tid) + "#gastlink")


def _guest_tournament(token):
    if not token or len(token) < 10:
        abort(404)
    t = get_db().execute("SELECT * FROM kt_tournaments WHERE guest_token=? AND source='live'", (token,)).fetchone()
    if not t:
        abort(404)
    return t


@bp.route("/gast/<token>")
def gast_tv(token):
    t = _guest_tournament(token)
    ctx = _live_context(t["id"])
    ctx["guest_token"] = token
    if request.args.get("partial"):
        return render_template("kicker/_tv_inner.html", **ctx)
    return render_template("kicker/tv.html", **ctx)


@bp.route("/gast/<token>/status.json")
def gast_status(token):
    return _status(_guest_tournament(token)["id"])


# --- Export (Kickertool-ähnliches JSON, z. B. als Sicherung) -----------------------

@bp.route("/turnier/<int:tid>/export.json")
def export(tid):
    db = get_db()
    t = db.execute("SELECT * FROM kt_tournaments WHERE id=?", (tid,)).fetchone()
    if not t:
        abort(404)
    matches = kstats.load_matches(None, None, tournament_id=tid, db=db)

    def team(side):
        return {"name": " | ".join(side), "players": [{"name": n} for n in side] if len(side) > 1 else []}

    def mrow(m):
        ts = None
        if m["played_at"]:
            try:
                ts = int(datetime.strptime(m["played_at"][:16], "%Y-%m-%d %H:%M").timestamp() * 1000)
            except ValueError:
                ts = None
        return {"team1": team(m["t1"]), "team2": team(m["t2"]), "result": [m["score1"], m["score2"]],
                "timeStart": ts, "timeEnd": ts, "valid": True}
    groups = defaultdict(list)
    ko = defaultdict(list)
    for m in matches:
        if m["stage"] == "gruppe":
            groups[(m["round_no"], str(m["round_no"]))].append(mrow(m))
        else:
            ko[(kmodes.play_order(m["stage"], m["round_no"]), m["round_label"])].append(mrow(m))
    if not t["ext_id"]:
        db.execute("UPDATE kt_tournaments SET ext_id=? WHERE id=?", (f"kcu-{tid}", tid))
        db.commit()
    standings, participants = [], []
    for i, s in enumerate(db.execute("SELECT * FROM kt_standings WHERE tournament_id=? ORDER BY place IS NULL, place", (tid,)), 1):
        pid = f"p{i}"
        standings.append({"_id": pid, "name": s["name"], "stats": {"place": s["place"]}})
        players = json.loads(s["players"]) if s["players"] else []
        participants.append({"_id": pid, "name": s["name"], "players": [{"name": n} for n in players]})
    data = {"_id": t["ext_id"] or f"kcu-{tid}", "name": t["name"], "mode": t["mode"], "nameType": t["name_type"],
            "createdAt": f"{t['date']}T12:00:00.000Z", "sport": "table_soccer", "source": "KCU-App",
            "qualifying": [{"standings": standings, "participants": participants,
                            "rounds": [{"name": k[1], "matches": v} for k, v in sorted(groups.items())]}],
            "eliminations": [{"levels": [{"name": k[1], "matches": v} for k, v in sorted(ko.items())]}] if ko else []}
    resp = jsonify(data)
    safe = "".join(c if c.isalnum() else "_" for c in t["name"])[:60]
    resp.headers["Content-Disposition"] = f'attachment; filename="{safe}_export.json"'
    return resp
