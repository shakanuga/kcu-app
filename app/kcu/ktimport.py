"""Import von Kickertool-Turnieren (JSON-Export und .ktool-Datei)."""
import json
import re
from datetime import datetime, timezone

DISCIPLINE_PATTERNS = [
    ("sonstiges", re.compile(r"^\s*test", re.I)),
    ("bierpong", re.compile(r"(^|[^a-z])bp([^a-z]|$)|bier\s*pong|beer\s*pong|pong", re.I)),
    ("flunky", re.compile(r"flunk|rover", re.I)),
]

MODE_LABEL = {
    "whist": "Monster-DYP",
    "monster_dyp": "Monster-DYP",
    "last_man_standing": "Mit Leben (Last Man Standing)",
    "lms": "Mit Leben (Last Man Standing)",
    "round_robin": "Jeder gegen jeden",
    "rounds": "Runden",
    "swiss": "Schweizer System",
    "elimination": "K.-o.-System",
    "double_elimination": "Doppel-K.-o.",
    "snake_draw": "Snake Draw",
    "live_dyp": "Monster-DYP",
}


class ImportError_(ValueError):
    pass


def guess_discipline(name):
    for disc, rx in DISCIPLINE_PATTERNS:
        if rx.search(name or ""):
            return disc
    return "kicker"


def _iso_from_ms(ms):
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return None


def _clean(name):
    return re.sub(r"\s+", " ", (name or "").strip())


def _side_from_export(team):
    """Teilnehmer aus dem JSON-Export -> Liste von Spielernamen."""
    if not team:
        return None
    players = team.get("players") or []
    if players:
        return [_clean(p.get("name")) for p in players if p.get("name")]
    return [_clean(team.get("name"))]


def _result(match):
    res = match.get("result")
    if isinstance(res, list) and len(res) == 2 and all(isinstance(x, (int, float)) for x in res):
        return int(res[0]), int(res[1])
    # Fallback: Tore aus den Sätzen summieren
    s1 = s2 = 0
    found = False
    for d in match.get("disciplines") or []:
        for st in d.get("sets") or []:
            sc = st.get("scores")
            if isinstance(sc, list) and len(sc) == 2:
                s1 += int(sc[0] or 0)
                s2 += int(sc[1] or 0)
                found = True
            elif "team1" in st and "team2" in st:
                s1 += int(st.get("team1") or 0)
                s2 += int(st.get("team2") or 0)
                found = True
    return (s1, s2) if found else (None, None)


def _valid(m, t1, t2, s1, s2):
    if not t1 or not t2 or m.get("skipped") or m.get("deactivated") or m.get("team1bye") or m.get("team2bye"):
        return False
    if s1 is None or s2 is None or (s1 == 0 and s2 == 0):
        return False
    return True


def parse_export(d):
    """Kickertool-JSON-Export (…_export.json)."""
    out = {"ext_id": d.get("_id"), "name": _clean(d.get("name")) or "Turnier", "mode": d.get("mode") or "?",
           "name_type": d.get("nameType"), "matches": [], "standings": []}
    created = (d.get("createdAt") or "")[:10]
    first_time = None
    seq = 0
    for g in d.get("qualifying") or []:
        part_players = {p.get("_id"): [_clean(x.get("name")) for x in (p.get("players") or [])]
                        for p in g.get("participants") or []}
        multi = len(d.get("qualifying") or []) > 1
        for st in g.get("standings") or []:
            stats = st.get("stats") or {}
            out["standings"].append({"name": _clean(st.get("name")),
                                     "place": None if multi else stats.get("place"),
                                     "players": part_players.get(st.get("_id")) or None})
        for ri, r in enumerate(g.get("rounds") or [], 1):
            for m in r.get("matches") or []:
                t1, t2 = _side_from_export(m.get("team1")), _side_from_export(m.get("team2"))
                s1, s2 = _result(m)
                if not _valid(m, t1, t2, s1, s2):
                    continue
                seq += 1
                played = _iso_from_ms(m.get("timeEnd") or m.get("timeStart"))
                if played and (first_time is None or played < first_time):
                    first_time = played
                out["matches"].append({"stage": "gruppe", "round_no": ri, "round_label": r.get("name") or str(ri),
                                       "seq": seq, "team1": t1, "team2": t2, "score1": s1, "score2": s2,
                                       "played_at": played})
    for e in d.get("eliminations") or []:
        levels = (e.get("leftLevels") or []) + (e.get("levels") or [])
        for li, lv in enumerate(levels, 1):
            for m in lv.get("matches") or []:
                t1, t2 = _side_from_export(m.get("team1")), _side_from_export(m.get("team2"))
                s1, s2 = _result(m)
                if not _valid(m, t1, t2, s1, s2):
                    continue
                seq += 1
                played = _iso_from_ms(m.get("timeEnd") or m.get("timeStart"))
                if played and (first_time is None or played < first_time):
                    first_time = played
                out["matches"].append({"stage": "ko", "round_no": li, "round_label": _ko_label(lv.get("name")),
                                       "seq": seq, "team1": t1, "team2": t2, "score1": s1, "score2": s2,
                                       "played_at": played})
        # K.-o.-Platzierungen (Finale/Spiel um Platz 3), falls keine Gruppenplätze da sind
        if not out["standings"]:
            out["standings"] = _ko_places(out["matches"])
    out["date"] = (first_time or "")[:10] or created or datetime.now().strftime("%Y-%m-%d")
    return out


def _ko_label(name):
    if not name:
        return "K.-o."
    n = name.upper()
    if n.startswith("FINALS-1-1") or n == "FINAL" or n.endswith("-1-1"):
        return "Finale"
    m = re.match(r"FINALS-1-(\d+)", n)
    if m:
        k = int(m.group(1))
        return {2: "Halbfinale", 4: "Viertelfinale", 8: "Achtelfinale"}.get(k, f"1/{k}-Finale")
    if "THIRD" in n or "3RD" in n:
        return "Spiel um Platz 3"
    return name


def _ko_places(matches):
    finals = [m for m in matches if m["stage"] == "ko" and m["round_label"] == "Finale"]
    if not finals:
        return []
    f = finals[-1]
    win, lose = (f["team1"], f["team2"]) if f["score1"] > f["score2"] else (f["team2"], f["team1"])
    return [{"name": " | ".join(win), "players": win, "place": 1},
            {"name": " | ".join(lose), "players": lose, "place": 2}]


def parse_ktool(d):
    """Interne Kickertool-Datei (.ktool)."""
    players = {p["_id"]: _clean(p.get("_name") or p.get("name")) for p in d.get("players") or []}
    teams = {}
    for t in d.get("teams") or []:
        pl = [players.get(x.get("_id")) for x in t.get("players") or [] if players.get(x.get("_id"))]
        teams[t["_id"]] = pl or ([_clean(t.get("_name"))] if t.get("_name") else [])

    def side(ref):
        if not ref:
            return None
        rid = ref.get("_id")
        if rid in teams:
            return teams[rid]
        if rid in players:
            return [players[rid]]
        return None

    out = {"ext_id": d.get("_id"), "name": _clean(d.get("name")) or "Turnier", "mode": d.get("mode") or "?",
           "name_type": d.get("nameType"), "matches": [], "standings": []}
    seq = 0
    first_time = None
    groups = [("gruppe", d.get("rounds") or [])]
    ko = d.get("ko") or []
    if ko and isinstance(ko, list):
        lv = []
        for k in ko:
            lv += (k.get("leftLevels") or []) + (k.get("levels") or []) if isinstance(k, dict) else []
        groups.append(("ko", lv))
    for stage, rounds in groups:
        for ri, r in enumerate(rounds, 1):
            for m in r.get("plays") or r.get("matches") or []:
                t1, t2 = side(m.get("team1")), side(m.get("team2"))
                s1, s2 = _result(m)
                if not _valid(m, t1, t2, s1, s2):
                    continue
                seq += 1
                played = _iso_from_ms(m.get("timeEnd") or m.get("timeStart"))
                if played and (first_time is None or played < first_time):
                    first_time = played
                label = r.get("name") or str(ri)
                out["matches"].append({"stage": stage, "round_no": ri,
                                       "round_label": _ko_label(label) if stage == "ko" else label,
                                       "seq": seq, "team1": t1, "team2": t2, "score1": s1, "score2": s2,
                                       "played_at": played})
    created = (d.get("created") or "")[:10]
    out["date"] = (first_time or "")[:10] or created or datetime.now().strftime("%Y-%m-%d")
    return out


def parse_file(raw_bytes, filename=""):
    try:
        d = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ImportError_(f"{filename}: keine gültige Kickertool-Datei (JSON erwartet).")
    if not isinstance(d, dict):
        raise ImportError_(f"{filename}: unbekanntes Format.")
    if "qualifying" in d or "eliminations" in d:
        t = parse_export(d)
    elif d.get("type") == "Tournament" or "plays" in json.dumps(d.get("rounds", []))[:2000]:
        t = parse_ktool(d)
    else:
        raise ImportError_(f"{filename}: unbekanntes Format – bitte „Export zu JSON“ oder die .ktool-Datei nehmen.")
    if not t.get("ext_id"):
        raise ImportError_(f"{filename}: Turnier-ID fehlt.")
    t["discipline"] = guess_discipline(t["name"])
    return t


def save_tournament(db, t, actor, discipline=None, replace=True):
    """Speichert ein geparstes Turnier. Rückgabe: (id, 'neu'|'aktualisiert'|'übersprungen')."""
    row = db.execute("SELECT id, source FROM kt_tournaments WHERE ext_id=?", (t["ext_id"],)).fetchone()
    disc = discipline or t.get("discipline") or "kicker"
    opts = json.dumps({"kickertool_mode": t["mode"]})
    if row:
        if row["source"] == "live" or not replace:   # in der App gespielte Turniere nie überschreiben
            return row["id"], "übersprungen"
        tid = row["id"]
        db.execute("UPDATE kt_tournaments SET name=?, date=?, mode=?, name_type=?, discipline=? WHERE id=?",
                   (t["name"], t["date"], t["mode"], t.get("name_type"), disc, tid))
        db.execute("DELETE FROM kt_matches WHERE tournament_id=?", (tid,))
        if t["standings"]:   # .ktool-Dateien haben keine Platzierungen -> vorhandene behalten
            db.execute("DELETE FROM kt_standings WHERE tournament_id=?", (tid,))
        state = "aktualisiert"
    else:
        tid = db.execute(
            "INSERT INTO kt_tournaments(ext_id, name, date, mode, name_type, discipline, source, status, options, created_by) "
            "VALUES (?,?,?,?,?,?, 'import', 'fertig', ?, ?)",
            (t["ext_id"], t["name"], t["date"], t["mode"], t.get("name_type"), disc, opts, actor)).lastrowid
        state = "neu"
    for m in t["matches"]:
        db.execute("INSERT INTO kt_matches(tournament_id, stage, round_no, round_label, seq, team1, team2, score1, score2, "
                   "status, played_at) VALUES (?,?,?,?,?,?,?,?,?, 'fertig', ?)",
                   (tid, m["stage"], m["round_no"], m["round_label"], m["seq"], json.dumps(m["team1"], ensure_ascii=False),
                    json.dumps(m["team2"], ensure_ascii=False), m["score1"], m["score2"], m["played_at"]))
    for s in t["standings"]:
        if not s.get("name"):
            continue
        db.execute("INSERT OR REPLACE INTO kt_standings(tournament_id, name, players, place) VALUES (?,?,?,?)",
                   (tid, s["name"], json.dumps(s["players"], ensure_ascii=False) if s.get("players") else None,
                    s.get("place")))
    return tid, state
