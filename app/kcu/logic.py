"""Rechenlogik: Kasse, nächste Sitzung, Datumshilfen."""
from datetime import date, datetime, timedelta

from .db import get_db

MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def ym(d):
    return f"{d.year:04d}-{d.month:02d}"


def parse_ym(s):
    try:
        y, m = s.split("-")[:2]
        return int(y), int(m)
    except Exception:
        return None


def months_between(start, end):
    """Anzahl Monate von start bis end (beide inklusive), Format YYYY-MM."""
    a, b = parse_ym(start), parse_ym(end)
    if not a or not b:
        return 0
    n = (b[0] - a[0]) * 12 + (b[1] - a[1]) + 1
    return max(n, 0)


def euro(v):
    v = float(v or 0)
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if v < 0 else "") + s + " €"


def date_de(s, with_weekday=False):
    if not s:
        return ""
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return s
    txt = f"{d.day}. {MONTHS[d.month - 1]} {d.year}"
    if with_weekday:
        txt = f"{WEEKDAYS[d.weekday()]}, {txt}"
    return txt


def ym_de(s):
    p = parse_ym(s or "")
    return f"{MONTHS[p[1] - 1]} {p[0]}" if p else ""


# --- Sitzung: jeden 2. Samstag im Monat (Satzung §2) ------------------------

def second_saturday(year, month):
    d = date(year, month, 1)
    first_sat = d + timedelta(days=(5 - d.weekday()) % 7)
    return first_sat + timedelta(days=7)


def _add_months(y, m, n):
    m += n
    while m > 12:
        y, m = y + 1, m - 12
    return y, m


def ensure_sessions(settings, months_ahead=2, today=None):
    """Legt die monatlichen Sitzungen (2. Samstag) als Termine an, falls sie fehlen.
    Verschobene oder abgesagte Sitzungen bleiben, wie der Vorstand sie gesetzt hat."""
    today = today or date.today()
    db = get_db()
    lead = int(settings.get("rsvp_tage_vorher") or 1)
    time = settings.get("session_time") or "19:30"
    changed = False
    for i in range(months_ahead + 1):
        y, m = _add_months(today.year, today.month, i)
        d = second_saturday(y, m)
        if d < today:
            continue
        exists = db.execute("SELECT 1 FROM events WHERE kind='sitzung' AND substr(date,1,7)=?",
                            (f"{y:04d}-{m:02d}",)).fetchone()
        if not exists:
            db.execute("INSERT INTO events(title, date, time, kind, rsvp, rsvp_until, sort) "
                       "VALUES ('Sitzung', ?, ?, 'sitzung', 1, ?, 50)",
                       (d.isoformat(), time, (d - timedelta(days=lead)).isoformat()))
            changed = True
    if changed:
        db.commit()


def next_session(settings, today=None):
    """Nächste (nicht abgesagte) Sitzung als Event-Zeile, oder None."""
    today = today or date.today()
    return get_db().execute(
        "SELECT * FROM events WHERE kind='sitzung' AND cancelled=0 AND date >= ? ORDER BY date LIMIT 1",
        (today.isoformat(),)).fetchone()


def is_regular_date(dstr):
    try:
        d = datetime.strptime(dstr, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return True
    return d == second_saturday(d.year, d.month)


def rsvp_summary(event_id):
    db = get_db()
    rows = db.execute(
        "SELECT r.*, m.short, m.name, m.number, m.status AS mstatus FROM event_rsvp r "
        "JOIN members m ON m.id=r.member_id WHERE r.event_id=? ORDER BY r.updated_at", (event_id,)).fetchall()
    answered = {r["member_id"] for r in rows}
    open_ = db.execute("SELECT * FROM members WHERE status='aktiv' ORDER BY sort, name").fetchall()
    open_ = [m for m in open_ if m["id"] not in answered]
    groups = {"ja": [], "vielleicht": [], "nein": []}
    for r in rows:
        groups.setdefault(r["status"], []).append(r)
    return {"groups": groups, "open": open_, "count": {k: len(v) for k, v in groups.items()}}


# --- Kasse -------------------------------------------------------------------

def member_account(member, settings, today=None):
    """Berechnet das Konto eines Mitglieds."""
    today = today or date.today()
    db = get_db()
    beitrag = float(settings.get("beitrag") or 0)
    start = settings.get("kasse_start") or ym(today)
    current = ym(today)

    months = 0
    if member["status"] == "aktiv" or (member["status"] == "ehemalig" and member["left_at"]):
        begin = max(start, member["joined"] or start)
        end = current
        if member["left_at"]:
            end = min(end, member["left_at"])
        months = months_between(begin, end) if begin <= end else 0
    dues = months * beitrag

    pen = db.execute("SELECT COALESCE(SUM(amount),0) s, COUNT(*) c FROM penalties WHERE member_id=?",
                     (member["id"],)).fetchone()
    paid = db.execute("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE member_id=?",
                      (member["id"],)).fetchone()["s"]
    opening = float(member["opening"] or 0)
    open_amount = round(opening + dues + pen["s"] - paid, 2)
    return {
        "member": member,
        "months": months,
        "dues": dues,
        "penalties": pen["s"],
        "penalty_count": pen["c"],
        "paid": paid,
        "opening": opening,
        "open": open_amount,
        "no_trip_subsidy": open_amount >= 50,              # Strafe 8
        "behind_3_months": beitrag > 0 and open_amount >= 3 * beitrag,  # Hinweis auf Strafe 9
    }


def all_accounts(settings):
    db = get_db()
    members = db.execute(
        "SELECT * FROM members WHERE status IN ('aktiv','ehren') OR id IN "
        "(SELECT member_id FROM penalties UNION SELECT member_id FROM payments) "
        "ORDER BY CASE status WHEN 'aktiv' THEN 0 WHEN 'ehren' THEN 1 ELSE 2 END, sort, name"
    ).fetchall()
    return [member_account(m, settings) for m in members]


# Kassenbuch-Einträge aus noch geheimen Projekten zählen öffentlich nicht mit
PUBLIC_LEDGER = ("(project_id IS NULL OR project_id IN "
                 "(SELECT id FROM projects WHERE status='freigegeben'))")


def cash_balance(settings, include_hidden=False):
    db = get_db()
    start = float(settings.get("kasse_startbestand") or 0)
    where = "1=1" if include_hidden else PUBLIC_LEDGER
    paid = db.execute("SELECT COALESCE(SUM(amount),0) FROM payments").fetchone()[0]
    inc = db.execute(f"SELECT COALESCE(SUM(amount),0) FROM ledger WHERE kind='einnahme' AND {where}").fetchone()[0]
    out = db.execute(f"SELECT COALESCE(SUM(amount),0) FROM ledger WHERE kind='ausgabe' AND {where}").fetchone()[0]
    return round(start + paid + inc - out, 2)


def project_totals(project_id):
    db = get_db()
    row = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN kind='ausgabe' THEN amount ELSE 0 END),0) out, "
        "COALESCE(SUM(CASE WHEN kind='einnahme' THEN amount ELSE 0 END),0) inc, COUNT(*) n "
        "FROM ledger WHERE project_id=?", (project_id,)).fetchone()
    return {"out": row["out"], "inc": row["inc"], "net": round(row["out"] - row["inc"], 2), "n": row["n"]}


# --- Jahresübersicht (Beiträge je Monat, wie die Papierliste) -------------------

def month_range(start, end):
    a, b = parse_ym(start), parse_ym(end)
    out = []
    if not a or not b:
        return out
    y, m = a
    while (y, m) <= b:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def member_grid(member, settings, year, today=None):
    """Monatsübersicht: Zahlungen mit Monatsangabe gelten für diesen Monat (wie auf der Papierliste),
    alle anderen werden den ältesten offenen Posten zugeordnet (Startsaldo, Beiträge, Strafen).

    Rückgabe: {monat: {"due": Beitrag, "dues_state": paid|partial|open|future_paid|none, "pens": [...]}}
    """
    today = today or date.today()
    db = get_db()
    beitrag = float(settings.get("beitrag") or 0)
    start = settings.get("kasse_start") or ym(today)
    current = ym(today)
    charges = []   # (sortkey, betrag, art, monat, info)
    opening = float(member["opening"] or 0)
    credit = 0.0
    if opening > 0:
        charges.append(("0000", opening, "start", None, "Startsaldo"))
    elif opening < 0:
        credit = -opening
    # Beiträge bis Jahresende (zukünftige nur, wenn vorausbezahlt)
    last = f"{max(int(year), int(current[:4])):04d}-12"
    begin = max(start, member["joined"] or start)
    end = last
    if member["left_at"]:
        end = min(end, member["left_at"])
    if member["status"] == "aktiv" or (member["status"] == "ehemalig" and member["left_at"]):
        for mo in month_range(begin, end):
            charges.append((mo + "-01", beitrag, "beitrag", mo, None))
    for p in db.execute("SELECT * FROM penalties WHERE member_id=? ORDER BY date, id", (member["id"],)):
        if p["amount"]:
            charges.append((p["date"], float(p["amount"]), "strafe", p["date"][:7], p["title"]))
    charges.sort(key=lambda c: (c[0], 0 if c[2] == "beitrag" else 1))
    rest = [c[1] for c in charges]          # noch offener Betrag je Posten
    pool = credit
    # 1) Zahlungen mit Monatsangabe zuerst auf diesen Monat (erst Beitrag, dann Strafen des Monats)
    for pay in db.execute("SELECT amount, period FROM payments WHERE member_id=? ORDER BY date, id", (member["id"],)):
        amt = float(pay["amount"] or 0)
        if pay["period"]:
            for i, c in enumerate(charges):
                if amt <= 0:
                    break
                if c[3] == pay["period"] and rest[i] > 0:
                    use = min(rest[i], amt)
                    rest[i] -= use
                    amt -= use
        pool += amt
    # 2) Alles Übrige der Reihe nach auf die ältesten offenen Posten
    for i, c in enumerate(charges):
        if pool <= 0:
            break
        use = min(rest[i], pool)
        rest[i] -= use
        pool -= use
    grid = {f"{year}-{m:02d}": {"due": 0.0, "paid": 0.0, "state": "none", "pens": []} for m in range(1, 13)}
    for i, (key, amount, kind, mo, info) in enumerate(charges):
        covered = amount - rest[i]
        if kind == "beitrag" and mo in grid:
            c = grid[mo]
            c["due"], c["paid"] = amount, covered
            if covered >= amount - 0.005:
                c["state"] = "future_paid" if mo > current else "paid"
            elif mo > current:
                c["state"] = "none"
            else:
                c["state"] = "partial" if covered > 0 else "open"
        elif kind == "strafe" and mo in grid:
            grid[mo]["pens"].append({"title": info, "amount": amount, "paid": covered >= amount - 0.005})
    return grid


# --- Geburtstage ----------------------------------------------------------------

def birthdays(days=14, today=None):
    """Anstehende Geburtstage aktiver Mitglieder (inkl. heute), sortiert nach Abstand."""
    today = today or date.today()
    out = []
    for m in get_db().execute("SELECT * FROM members WHERE status IN ('aktiv','ehren') AND birthday IS NOT NULL AND birthday<>''"):
        try:
            b = date.fromisoformat(m["birthday"])
        except ValueError:
            continue
        for y in (today.year, today.year + 1):
            try:
                nxt = b.replace(year=y)
            except ValueError:          # 29. Februar
                nxt = date(y, 3, 1)
            if nxt >= today:
                break
        delta = (nxt - today).days
        if delta <= days:
            age = (nxt.year - b.year) if b.year > 1900 else None
            out.append({"m": m, "date": nxt, "days": delta, "age": age})
    return sorted(out, key=lambda x: x["days"])
