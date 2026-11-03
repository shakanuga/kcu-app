"""Turniermodi wie im Kickertool: Teams bilden, K.-o., Doppel-K.-o., Schweizer System.

Spiele liegen in kt_matches. Bei Turnierbäumen gilt:
  stage  = 'ko' | 'ko3' (Spiel um Platz 3) | 'wb' (Gewinnerrunde) | 'lb' (Verliererrunde) | 'gf' (großes Finale)
  round_no = Runde innerhalb der Stufe (ab 1), seq = Position in der Runde (ab 0)
  team1/team2 = JSON-Liste der Spieler, [] = noch offen, ["__FREILOS__"] = Freilos
  status = 'wartet' (Gegner fehlt) | 'offen' | 'fertig' | 'freilos'
"""
import json
import math
import random
from collections import defaultdict

BYE = "__FREILOS__"
BRACKET_MODES = ("ko", "doko")


def is_bye(side):
    return side == [BYE]


# --- Teams bilden ----------------------------------------------------------------

def build_teams(names, method="zufall", manual="", strength=None, size=2):
    """Liste von Teams (Listen von Spielernamen). method: zufall | manuell | snake."""
    teams, taken = [], set()
    if manual.strip():
        for line in manual.splitlines():
            pl = [" ".join(x.split()) for x in line.replace("&", ",").replace("/", ",").split(",") if x.strip()]
            if pl:
                teams.append(pl)
                taken |= set(pl)
    rest = [n for n in names if n not in taken]
    if method == "snake" and strength is not None:
        # Stärkster mit Schwächstem, Zweitstärkster mit Zweitschwächstem … (Snake Draw)
        rest.sort(key=lambda n: (-strength.get(n, 0), random.random()))
        rand = []
        while len(rest) >= 2:
            rand.append([rest.pop(0), rest.pop(-1)])
        if rest:
            rand.append(rest)
    else:
        random.shuffle(rest)
        rand = [rest[i:i + size] for i in range(0, len(rest), size)]
    if rand and len(rand[-1]) == 1 and len(rand) > 1:
        last = rand.pop()
        rand[-1] = rand[-1] + last
    return teams + rand


# --- K.-o. und Doppel-K.-o. --------------------------------------------------------

def _seed_order(size):
    """Klassische Setzliste: 1 gegen size, 2 gegen size-1 … so verteilt, dass Topgesetzte sich spät treffen."""
    order = [1]
    while len(order) < size:
        n = len(order) * 2
        order = [x for s in order for x in (s, n + 1 - s)]
    return order


def ko_label(r, rounds):
    left = rounds - r
    return {0: "Finale", 1: "Halbfinale", 2: "Viertelfinale", 3: "Achtelfinale"}.get(left, f"Runde {r}")


def bracket_size(n):
    return 1 << max(1, math.ceil(math.log2(max(n, 2))))


def create_bracket(entries, mode, third_place=True):
    """entries: Liste von Teams (Spielerlisten) in Setzreihenfolge. Rückgabe: Liste von Spiel-Dicts."""
    S = bracket_size(len(entries))
    R = int(math.log2(S))
    seeds = _seed_order(S)
    slots = [entries[s - 1] if s <= len(entries) else [BYE] for s in seeds]
    matches = []
    first = "ko" if mode == "ko" else "wb"
    for r in range(1, R + 1):
        for i in range(S >> r):
            m = {"stage": first, "round_no": r, "seq": i, "t1": [], "t2": [],
                 "label": ko_label(r, R) if mode == "ko" else ("Gewinner-Finale" if r == R else f"Gewinnerrunde {r}")}
            if r == 1:
                m["t1"], m["t2"] = slots[2 * i], slots[2 * i + 1]
            matches.append(m)
    if mode == "ko" and third_place and R >= 2:
        matches.append({"stage": "ko3", "round_no": R, "seq": 0, "t1": [], "t2": [], "label": "Spiel um Platz 3"})
    if mode == "doko":
        for k in range(1, 2 * (R - 1) + 1):
            if k == 1:
                count = S >> 2
            elif k % 2 == 0:
                count = S >> (k // 2 + 1)
            else:
                count = S >> ((k - 1) // 2 + 2)
            for i in range(count):
                matches.append({"stage": "lb", "round_no": k, "seq": i, "t1": [], "t2": [],
                                "label": "Verlierer-Finale" if k == 2 * (R - 1) else f"Verliererrunde {k}"})
        matches.append({"stage": "gf", "round_no": 1, "seq": 0, "t1": [], "t2": [], "label": "Großes Finale"})
    return matches, S


def targets(mode, S, stage, r, i):
    """Wohin gehen Sieger und Verlierer? Rückgabe: (sieger_ziel, verlierer_ziel), Ziel = (stage, runde, pos, slot)."""
    R = int(math.log2(S))
    if mode == "ko":
        if stage == "ko":
            win = ("ko", r + 1, i // 2, i % 2) if r < R else None
            lose = ("ko3", R, 0, i % 2) if r == R - 1 and R >= 2 else None
            return win, lose
        return None, None
    # Doppel-K.-o.
    if stage == "wb":
        win = ("wb", r + 1, i // 2, i % 2) if r < R else ("gf", 1, 0, 0)
        if r == 1:
            lose = ("lb", 1, i // 2, i % 2) if R >= 2 else ("gf", 1, 0, 1)
        else:
            count = S >> r
            pos = (count - 1 - i) if r % 2 == 0 else i       # Seitenwechsel gegen schnelle Revanchen
            lose = ("lb", 2 * (r - 1), pos, 1)
        return win, lose
    if stage == "lb":
        last = 2 * (R - 1)
        if r == last:
            return ("gf", 1, 0, 1), None
        if r % 2 == 1:      # ungerade Runde -> nächste (Einsteiger-)Runde, gleiche Position, Slot 0
            return ("lb", r + 1, i, 0), None
        return ("lb", r + 1, i // 2, i % 2), None
    return None, None


def play_order(stage, r):
    """Sortierschlüssel, damit Spiele in sinnvoller Reihenfolge drankommen."""
    if stage in ("ko", "wb"):
        return (2 * r - 1, 0)
    if stage == "lb":
        return (r + 1, 1)
    if stage == "ko3":
        return (2 * r - 1, 1)
    if stage == "gf":
        return (999, 0)
    return (r, 0)


def winner_loser(m):
    """(sieger, verlierer) als Spielerlisten – auch bei Freilos."""
    if m["status"] == "freilos":
        return (m["t2"], m["t1"]) if is_bye(m["t1"]) else (m["t1"], m["t2"])
    if m["score1"] is None:
        return None, None
    return (m["t1"], m["t2"]) if m["score1"] > m["score2"] else (m["t2"], m["t1"])


def placements(mode, S, matches):
    """Endplatzierungen aus dem Turnierbaum. Rückgabe: {tuple(spieler): platz}."""
    R = int(math.log2(S))
    out = {}
    elim = {}       # Team -> Ausscheide-Schlüssel (größer = später ausgeschieden)
    for m in matches:
        w, l = winner_loser(m)
        if not l or is_bye(l):
            continue
        key = play_order(m["stage"], m["round_no"])
        if mode == "ko" and m["stage"] == "ko3":
            continue
        elim[tuple(l)] = max(elim.get(tuple(l), (0, 0)), key)
    final = next((m for m in matches if m["stage"] == ("ko" if mode == "ko" else "gf") and
                  (mode != "ko" or m["round_no"] == R)), None)
    if final:
        w, l = winner_loser(final)
        if w and not is_bye(w):
            out[tuple(w)] = 1
            if l and not is_bye(l):
                out[tuple(l)] = 2
    if mode == "ko":
        third = next((m for m in matches if m["stage"] == "ko3"), None)
        if third and third["status"] in ("fertig", "freilos"):
            w, l = winner_loser(third)
            if w and not is_bye(w):
                out[tuple(w)] = 3
            if l and not is_bye(l):
                out[tuple(l)] = 4
    rest = sorted(((t, k) for t, k in elim.items() if t not in out), key=lambda x: x[1], reverse=True)
    base = len(out)
    place = base
    prev = None
    for i, (t, k) in enumerate(rest):
        if k != prev:
            place = base + i + 1
        out.setdefault(t, place)
        prev = k
    return out


# --- Schweizer System --------------------------------------------------------------

def swiss_pairs(entries, matches, team_key):
    """entries: {key: spielerliste}. Rückgabe: Liste von (key_a, key_b) bzw. (key, None) für Freilos."""
    pts = defaultdict(float)
    diff = defaultdict(int)
    met = defaultdict(set)
    byes = defaultdict(int)
    for m in matches:
        k1, k2 = team_key(m["t1"]), team_key(m["t2"])
        if m["status"] == "freilos":
            real = k2 if is_bye(m["t1"]) else k1
            pts[real] += 1
            byes[real] += 1
            continue
        if m["status"] != "fertig" or m["score1"] is None:
            continue
        met[k1].add(k2)
        met[k2].add(k1)
        if m["score1"] > m["score2"]:
            pts[k1] += 1
        else:
            pts[k2] += 1
        diff[k1] += m["score1"] - m["score2"]
        diff[k2] += m["score2"] - m["score1"]
    keys = list(entries)
    random.shuffle(keys)
    keys.sort(key=lambda k: (-pts[k], -diff[k]))
    bye = None
    if len(keys) % 2:
        cand = sorted(reversed(keys), key=lambda k: byes[k])
        bye = cand[0]
        keys.remove(bye)

    def solve(pool):
        if not pool:
            return []
        a = pool[0]
        for strict in (True, False):
            for j in range(1, len(pool)):
                b = pool[j]
                if strict and b in met[a]:
                    continue
                rest = solve(pool[1:j] + pool[j + 1:])
                if rest is not None:
                    return [(a, b)] + rest
        return None
    pairs = solve(keys) or [(keys[i], keys[i + 1]) for i in range(0, len(keys), 2)]
    if bye:
        pairs.append((bye, None))
    return pairs


def swiss_rounds_default(n):
    return max(3, math.ceil(math.log2(max(n, 2))) + 1)


def dumps(x):
    return json.dumps(x, ensure_ascii=False)
