"""Kicker-Statistik: Namen auflösen und Kennzahlen aus allen Spielen berechnen."""
import json
from collections import defaultdict
from datetime import datetime, timedelta

from .db import get_db

DISCIPLINE_LABEL = {"kicker": "Kicker", "bierpong": "Bierpong", "flunky": "Flunkyball", "sonstiges": "Sonstiges / Test"}


class Resolver:
    """Spielername -> Mitglied (über Spitznamen-Tabelle, Rufname, Vorname, vollen Namen)."""

    def __init__(self, db=None):
        db = db or get_db()
        self.members = {r["id"]: dict(r) for r in db.execute("SELECT id, name, short, number, status FROM members")}
        self.alias = {}
        self.ignored = set()
        for r in db.execute("SELECT alias, member_id, ignore FROM player_aliases"):
            if r["ignore"]:
                self.ignored.add(r["alias"])
            elif r["member_id"]:
                self.alias[r["alias"]] = r["member_id"]
        self.auto = {}
        first_count = defaultdict(int)
        for m in self.members.values():
            first_count[(m["name"] or "").split(" ")[0].lower()] += 1
        for m in self.members.values():
            for key in ((m["short"] or "").lower(), (m["name"] or "").lower()):
                if key:
                    self.auto.setdefault(key, m["id"])
            first = (m["name"] or "").split(" ")[0].lower()
            if first and first_count[first] == 1:
                self.auto.setdefault(first, m["id"])

    def member_id(self, name):
        k = (name or "").strip().lower()
        if not k or k in self.ignored:
            return None
        return self.alias.get(k) or self.auto.get(k)

    def label(self, name):
        mid = self.member_id(name)
        if mid:
            m = self.members[mid]
            return m["short"] or m["name"]
        return name

    def known(self, name):
        k = (name or "").strip().lower()
        return k in self.ignored or self.member_id(name) is not None


def _evening(first_played, date):
    """Spielabend eines Turniers: Startzeit minus 8 Stunden (00:30 Uhr gehört noch zum Vorabend)."""
    if first_played:
        try:
            return (datetime.strptime(first_played[:16], "%Y-%m-%d %H:%M") - timedelta(hours=8)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return date


def counting_info(db=None):
    """Welche Turniere zählen für die Statistik? Rückgabe: {id: (zählt, grund)}.

    Regel: Pro Spielabend und Disziplin zählt der erste gespielte Modus, alles danach ist „zum Spaß“.
    Von Hand gesetzte Wertung (Turnierseite) geht vor."""
    db = db or get_db()
    rows = db.execute(
        "SELECT t.id, t.date, t.discipline, t.counts, "
        "(SELECT MIN(played_at) FROM kt_matches m WHERE m.tournament_id=t.id AND m.status='fertig') first, "
        "(SELECT COUNT(*) FROM kt_matches m WHERE m.tournament_id=t.id AND m.status='fertig' AND m.score1 IS NOT NULL) n "
        "FROM kt_tournaments t").fetchall()
    groups = defaultdict(list)
    for r in rows:
        groups[(r["discipline"], _evening(r["first"], r["date"]))].append(r)
    out = {}
    for items in groups.values():
        manual_yes = any(r["counts"] == 1 for r in items)
        auto = None
        if not manual_yes:
            cands = sorted((r for r in items if r["n"] and r["counts"] is None),
                           key=lambda r: (r["first"] or "9999", r["id"]))
            auto = cands[0]["id"] if cands else None
        for r in items:
            if r["counts"] == 1:
                out[r["id"]] = (True, "manuell")
            elif r["counts"] == 0:
                out[r["id"]] = (False, "manuell")
            elif r["id"] == auto:
                out[r["id"]] = (True, "auto")
            else:
                out[r["id"]] = (False, "auto")
    return out


def counted_ids(db=None):
    return {tid for tid, (yes, _) in counting_info(db).items() if yes}


def load_matches(discipline="kicker", year=None, tournament_id=None, db=None, counted_only=False):
    db = db or get_db()
    q = ("SELECT m.*, t.name AS t_name, t.date AS t_date, t.discipline FROM kt_matches m "
         "JOIN kt_tournaments t ON t.id=m.tournament_id WHERE m.status='fertig' AND m.score1 IS NOT NULL")
    args = []
    if discipline:
        q += " AND t.discipline=?"
        args.append(discipline)
    if year:
        q += " AND substr(t.date,1,4)=?"
        args.append(str(year))
    if tournament_id:
        q += " AND t.id=?"
        args.append(tournament_id)
    q += " ORDER BY t.date, t.id, m.stage DESC, m.round_no, m.seq"
    keep = counted_ids(db) if counted_only else None
    rows = []
    for r in db.execute(q, args):
        if keep is not None and r["tournament_id"] not in keep:
            continue
        d = dict(r)
        d["t1"] = json.loads(r["team1"])
        d["t2"] = json.loads(r["team2"])
        rows.append(d)
    return rows


def _blank():
    return {"games": 0, "wins": 0, "losses": 0, "draws": 0, "gf": 0, "ga": 0, "shutout_wins": 0, "shutout_losses": 0, "kasten_wins": 0, "kasten_losses": 0,
            "tournaments": set(), "streak": 0, "best_streak": 0, "form": []}


def compute(matches, resolver, min_pair=5):
    """Kennzahlen je Mitglied. Gäste werden ignoriert."""
    P = defaultdict(_blank)
    partners = defaultdict(lambda: [0, 0])      # (a,b) -> [spiele, siege]
    opp = defaultdict(lambda: [0, 0])           # (a,b) -> [spiele gegen b, siege gegen b]
    records = {"biggest": None}
    for m in matches:
        s1, s2 = m["score1"], m["score2"]
        sides = [[resolver.member_id(n) for n in m["t1"]], [resolver.member_id(n) for n in m["t2"]]]
        for si, side in enumerate(sides):
            gf, ga = (s1, s2) if si == 0 else (s2, s1)
            won, lost = gf > ga, gf < ga
            for mid in side:
                if not mid:
                    continue
                p = P[mid]
                p["games"] += 1
                p["gf"] += gf
                p["ga"] += ga
                p["tournaments"].add(m["tournament_id"])
                if won:
                    p["wins"] += 1
                    p["streak"] = p["streak"] + 1 if p["streak"] >= 0 else 1
                    if ga == 0 and gf >= 5:
                        p["shutout_wins"] += 1
                    if ga == 0 and gf >= 10:
                        p["kasten_wins"] += 1
                elif lost:
                    p["losses"] += 1
                    p["streak"] = p["streak"] - 1 if p["streak"] <= 0 else -1
                    if gf == 0 and ga >= 5:
                        p["shutout_losses"] += 1
                    if gf == 0 and ga >= 10:
                        p["kasten_losses"] += 1
                else:
                    p["draws"] += 1
                    p["streak"] = 0
                p["best_streak"] = max(p["best_streak"], p["streak"])
                p["form"].append("S" if won else ("N" if lost else "U"))
                for mate in side:
                    if mate and mate != mid:
                        partners[(mid, mate)][0] += 1
                        partners[(mid, mate)][1] += 1 if won else 0
                for o in sides[1 - si]:
                    if o:
                        opp[(mid, o)][0] += 1
                        opp[(mid, o)][1] += 1 if won else 0
        diff = abs(s1 - s2)
        if records["biggest"] is None or diff > records["biggest"]["diff"]:
            win, lose = (m["t1"], m["t2"]) if s1 >= s2 else (m["t2"], m["t1"])
            records["biggest"] = {"diff": diff, "match": m, "win": win, "lose": lose,
                                  "score": f"{max(s1, s2)}:{min(s1, s2)}"}
    out = {}
    for mid, p in P.items():
        g = p["games"] or 1
        p["win_pct"] = round(100 * p["wins"] / g)
        p["goal_diff"] = p["gf"] - p["ga"]
        p["gf_avg"] = round(p["gf"] / g, 1)
        p["ga_avg"] = round(p["ga"] / g, 1)
        p["stars"] = p["wins"]
        p["n_tournaments"] = len(p["tournaments"])
        p["form"] = p["form"][-10:]
        mine_p = [(b, v) for (a, b), v in partners.items() if a == mid]
        mine_o = [(b, v) for (a, b), v in opp.items() if a == mid]
        p["partners"] = sorted(({"id": b, "games": v[0], "wins": v[1], "pct": round(100 * v[1] / v[0])}
                                for b, v in mine_p), key=lambda x: (-x["games"], -x["pct"]))
        p["opponents"] = sorted(({"id": b, "games": v[0], "wins": v[1], "pct": round(100 * v[1] / v[0])}
                                 for b, v in mine_o), key=lambda x: (-x["games"], x["pct"]))
        good = [x for x in p["partners"] if x["games"] >= min_pair]
        p["best_partner"] = max(good, key=lambda x: (x["pct"], x["games"])) if good else None
        p["worst_partner"] = min(good, key=lambda x: (x["pct"], -x["games"])) if good else None
        rivals = [x for x in p["opponents"] if x["games"] >= min_pair]
        p["nemesis"] = min(rivals, key=lambda x: (x["pct"], -x["games"])) if rivals else None
        p["victim"] = max(rivals, key=lambda x: (x["pct"], x["games"])) if rivals else None
        out[mid] = p
    return out, records


def titles(discipline="kicker", year=None, db=None, resolver=None, counted_only=False):
    """Turniersiege und Podestplätze je Mitglied aus den Platzierungen."""
    db = db or get_db()
    keep = counted_ids(db) if counted_only else None
    resolver = resolver or Resolver(db)
    q = ("SELECT s.*, t.date, t.name AS t_name FROM kt_standings s JOIN kt_tournaments t ON t.id=s.tournament_id "
         "WHERE s.place IS NOT NULL AND s.place <= 3")
    args = []
    if discipline:
        q += " AND t.discipline=?"
        args.append(discipline)
    if year:
        q += " AND substr(t.date,1,4)=?"
        args.append(str(year))
    res = defaultdict(lambda: {"gold": 0, "silver": 0, "bronze": 0, "won": []})
    for r in db.execute(q, args):
        if keep is not None and r["tournament_id"] not in keep:
            continue
        names = json.loads(r["players"]) if r["players"] else _split_team(r["name"], resolver)
        for n in names:
            mid = resolver.member_id(n)
            if not mid:
                continue
            key = {1: "gold", 2: "silver", 3: "bronze"}[r["place"]]
            res[mid][key] += 1
            if r["place"] == 1:
                res[mid]["won"].append({"id": r["tournament_id"], "name": r["t_name"], "date": r["date"]})
    return res


def _split_team(name, resolver):
    for sep in (" | ", "/", " und ", " & ", "-", ","):
        if sep in name:
            parts = [x.strip() for x in name.split(sep) if x.strip()]
            if parts and all(resolver.member_id(x) for x in parts):
                return parts
    return [name]


def standings_live(matches, participants, mode, lives_start=None):
    """Tabelle für eigene Turniere (Einzelwertung je Spieler bzw. je Team)."""
    tab = {}
    order = {p["name"]: i for i, p in enumerate(participants)}
    for p in participants:
        tab[p["key"]] = {"name": p["key"], "label": p["label"], "games": 0, "wins": 0, "losses": 0, "gf": 0, "ga": 0,
                         "points": 0, "lives": p.get("lives"), "out_round": p.get("out_round"), "members": p["members"]}
    opps = defaultdict(list)
    for m in matches:
        if m["status"] == "freilos" and mode == "swiss":
            for k in (m["k1"] or m["k2"]):
                if k in tab:
                    tab[k]["wins"] += 1
                    tab[k]["points"] += 1
                    tab[k]["byes"] = tab[k].get("byes", 0) + 1
            continue
        if m["status"] != "fertig" or m["score1"] is None:
            continue
        for a in m["k1"]:
            opps[a].extend(m["k2"])
        for b in m["k2"]:
            opps[b].extend(m["k1"])
        for side, gf, ga in ((m["k1"], m["score1"], m["score2"]), (m["k2"], m["score2"], m["score1"])):
            for k in side:
                if k not in tab:
                    continue
                t = tab[k]
                t["games"] += 1
                t["gf"] += gf
                t["ga"] += ga
                if gf > ga:
                    t["wins"] += 1
                    t["points"] += 1
                elif gf < ga:
                    t["losses"] += 1
    rows = list(tab.values())
    for r in rows:
        r["diff"] = r["gf"] - r["ga"]
        r["buchholz"] = sum(tab[o]["points"] for o in opps[r["name"]] if o in tab)
    if mode == "lms":
        rows.sort(key=lambda r: (-(r["lives"] or 0), -(r["out_round"] or 10 ** 6), -r["wins"], -r["diff"], -r["gf"]))
    elif mode == "swiss":
        rows.sort(key=lambda r: (-r["points"], -r["buchholz"], -r["diff"], -r["gf"], order.get(r["name"], 0)))
    else:
        rows.sort(key=lambda r: (-r["points"], -r["diff"], -r["gf"], r["games"], order.get(r["name"], 0)))
    place = 0
    prev = None
    for i, r in enumerate(rows, 1):
        key = ((r["lives"], r["out_round"], r["wins"], r["diff"]) if mode == "lms" else
               (r["points"], r["buchholz"], r["diff"], r["gf"]) if mode == "swiss" else (r["points"], r["diff"], r["gf"]))
        if key != prev:
            place = i
        r["place"] = place
        prev = key
    return rows
