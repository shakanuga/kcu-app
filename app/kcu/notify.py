"""Benachrichtigungen per Push und E-Mail – sofort (neue Abstimmung, Termin verschoben) oder geplant (Erinnerungen)."""
import fcntl
import logging
import os
import smtplib
import threading
import time
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlparse

from flask import current_app

from . import webpush
from .db import get_db, get_settings

log = logging.getLogger("kcu.notify")

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

# Push-Dienste der Browser-Hersteller – an andere Adressen schickt der Server nichts (Schutz vor Missbrauch)
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
              "web.push.apple.com", "api.push.apple.com", "notify.windows.com", "push.api.chromecast.com")


def valid_endpoint(url):
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def now_local():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def short_date(iso):
    try:
        d = date.fromisoformat(iso[:10])
    except (TypeError, ValueError):
        return iso or ""
    return f"{WEEKDAYS[d.weekday()]}, {d.day:02d}.{d.month:02d}."


_vapid = None
_vapid_lock = threading.Lock()


def vapid():
    global _vapid
    with _vapid_lock:
        if _vapid is None:
            _vapid = webpush.Vapid(current_app.config["DATA_DIR"])
        return _vapid


def smtp_config(s=None):
    s = s or get_settings()
    cfg = {k: (os.environ.get("KCU_" + k.upper()) or s.get(k) or "").strip()
           for k in ("smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from", "smtp_security")}
    cfg["ok"] = bool(cfg["smtp_host"] and cfg["smtp_from"])
    return cfg


def send_mail(to, subject, text, s=None, raise_errors=False, html=None):
    cfg = smtp_config(s)
    if not cfg["ok"] or not to:
        if raise_errors:
            raise RuntimeError("SMTP-Server oder Absender fehlt in den Einstellungen.")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["smtp_from"]
    msg["To"] = to
    from email.utils import formatdate, make_msgid
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(cfg["smtp_from"].split("@")[-1].strip("> ") or "kcu.local"))
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    port = int(cfg["smtp_port"] or 587)
    try:
        if cfg["smtp_security"] == "ssl" or port == 465:
            srv = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=15)
        else:
            srv = smtplib.SMTP(cfg["smtp_host"], port, timeout=15)
            if cfg["smtp_security"] != "none":
                srv.starttls()
        if cfg["smtp_user"]:
            srv.login(cfg["smtp_user"], cfg["smtp_pass"])
        srv.send_message(msg)
        srv.quit()
        return True
    except (smtplib.SMTPException, OSError) as e:
        log.warning("Mail an %s fehlgeschlagen: %s", to, e)
        if raise_errors:
            raise
        return False


def _claim(key, info=""):
    """True, wenn diese Benachrichtigung noch nicht verschickt wurde (und markiert sie)."""
    db = get_db()
    cur = db.execute("INSERT OR IGNORE INTO notify_log(key, info) VALUES (?, ?)", (key, info[:200]))
    db.commit()
    return cur.rowcount == 1


def notify_mode(s=None):
    """aus | admins (Testbetrieb: nur Admins bekommen etwas) | alle"""
    m = (s or get_settings()).get("notify_mode") or "admins"
    return m if m in ("aus", "admins", "alle") else "admins"


def _admin_member_ids():
    from .auth import admin_emails
    db = get_db()
    ids = {r["member_id"] for r in db.execute("SELECT member_id FROM member_roles WHERE role='admin'")}
    mails = admin_emails()
    for r in db.execute("SELECT id, email FROM members WHERE email IS NOT NULL AND email<>''"):
        if r["email"].lower() in mails:
            ids.add(r["id"])
    return ids


def _plain_title(title):
    """Emoji am Anfang für den Mail-Betreff entfernen."""
    t = title.strip()
    while t and not t[0].isalnum() and t[0] not in "„\"(":
        t = t[1:].lstrip()
    return t or title


def mail_html(title, body, url, s):
    import html as _h
    base = (s.get("app_url") or "").rstrip("/")
    club = _h.escape(s.get("club_name") or "Kicker Club United")
    link = f"{base}{url}" if base else ""
    btn = (f'<p style="margin:28px 0 8px"><a href="{_h.escape(link)}" style="background:#e44235;color:#ffffff;'
           f'text-decoration:none;font-weight:bold;padding:12px 22px;border-radius:8px;display:inline-block">In der App öffnen</a></p>'
           if link else "")
    prefs = (f'<a href="{_h.escape(base)}/benachrichtigungen" style="color:#6fa3d1">Benachrichtigungen einstellen</a>'
             if base else "Benachrichtigungen kannst du in der App unter „Benachrichtigungen“ einstellen.")
    return f"""<!doctype html><html lang="de"><body style="margin:0;padding:0;background:#f2f2f4">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f2f4;padding:24px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#1d1d21">
<tr><td style="background:#111113;padding:18px 24px;border-bottom:4px solid #e44235">
<span style="color:#ffffff;font-size:22px;font-weight:bold;letter-spacing:1px">KCU</span>
<span style="color:#9b9ba3;font-size:12px;letter-spacing:2px;text-transform:uppercase;padding-left:8px">{club}</span></td></tr>
<tr><td style="padding:28px 24px 8px">
<h1 style="margin:0 0 14px;font-size:21px;line-height:1.3;color:#111113">{_h.escape(_plain_title(title))}</h1>
<p style="margin:0;font-size:16px;line-height:1.55;color:#33333a">{_h.escape(body)}</p>{btn}
</td></tr>
<tr><td style="padding:20px 24px 24px;font-size:12px;line-height:1.5;color:#8a8a92;border-top:1px solid #ececf0">
Diese Nachricht wurde automatisch von der Vereins-App des {club} verschickt. {prefs}
</td></tr></table></td></tr></table></body></html>"""


def mail_text(title, body, url, s):
    base = (s.get("app_url") or "").rstrip("/")
    club = s.get("club_name") or "Kicker Club United"
    lines = [_plain_title(title), "", body]
    if base:
        lines += ["", f"In der App öffnen: {base}{url}"]
    lines += ["", "—", f"Automatische Nachricht der Vereins-App des {club}.",
              f"Benachrichtigungen einstellen: {base}/benachrichtigungen" if base else "Benachrichtigungen kannst du in der App einstellen."]
    return "\n".join(lines)


def deliver(member_ids, title, body, url="/", key=None, tag=None, force=False):
    """Verschickt an die angegebenen Mitglieder (Push + ggf. Mail). Rückgabe: Anzahl erreichte Mitglieder.

    Im Testbetrieb (notify_mode=admins) gehen Nachrichten nur an Admins, bei „aus“ an niemanden.
    force=True (Test-Knöpfe) umgeht das.
    """
    db = get_db()
    s = get_settings()
    mode = notify_mode(s)
    ids = set(member_ids)
    if not force:
        if mode == "aus":
            return 0
        if mode == "admins":
            ids &= _admin_member_ids()
            key = f"test:{key}" if key else None   # im Echtbetrieb später trotzdem an alle
    if not ids:
        return 0
    if key and not _claim(key, title):
        return 0
    subject = s.get("smtp_from") or "mailto:kcu@example.org"
    subject = subject if subject.startswith("mailto:") else "mailto:" + subject.split("<")[-1].strip(">").strip()
    reached = 0
    for mid in ids:
        pref = db.execute("SELECT * FROM notify_prefs WHERE member_id=?", (mid,)).fetchone()
        push_on = pref["push_on"] if pref else 1
        email_on = pref["email_on"] if pref else 0
        got = False
        if push_on:
            for sub in db.execute("SELECT * FROM push_subs WHERE member_id=?", (mid,)).fetchall():
                ok, gone = webpush.send(vapid(), sub, {"title": title, "body": body, "url": url, "tag": tag or key or url},
                                        subject=subject)
                if gone:
                    db.execute("DELETE FROM push_subs WHERE id=?", (sub["id"],))
                got = got or ok
        if email_on:
            m = db.execute("SELECT email FROM members WHERE id=?", (mid,)).fetchone()
            if m and m["email"]:
                got = send_mail(m["email"], f"KCU · {_plain_title(title)}", mail_text(title, body, url, s), s,
                                html=mail_html(title, body, url, s)) or got
        reached += 1 if got else 0
    db.commit()
    return reached


def deliver_async(member_ids, title, body, url="/", key=None):
    """Aus einer Seitenanfrage heraus verschicken, ohne dass der Nutzer warten muss."""
    app = current_app._get_current_object()
    ids = list(member_ids)

    def run():
        with app.app_context():
            try:
                deliver(ids, title, body, url, key)
            except Exception:  # noqa: BLE001 – Benachrichtigungen dürfen die App nie stören
                log.exception("Benachrichtigung fehlgeschlagen")
    if app.testing or os.environ.get("KCU_NOTIFY_SYNC"):
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


def active_member_ids(exclude=None):
    return [r["id"] for r in get_db().execute("SELECT id FROM members WHERE status='aktiv'") if r["id"] != exclude]


def vorstand_ids(exclude=None):
    return [r["member_id"] for r in get_db().execute(
        "SELECT r.member_id FROM member_roles r JOIN members m ON m.id=r.member_id WHERE r.role='vorstand' AND m.status='aktiv'")
        if r["member_id"] != exclude]


# --- Auslöser aus der App -----------------------------------------------------

def on_new_poll(poll_id, title, audience, creator_id, creator_name):
    ids = vorstand_ids(creator_id) if audience == "vorstand" else active_member_ids(creator_id)
    deliver_async(ids, "🗳️ Neue Abstimmung", f"{creator_name} fragt: {title}", f"/abstimmungen/{poll_id}",
                  key=f"poll-new-{poll_id}")


def on_event_changed(e, old):
    """Termin verschoben / abgesagt / neu angelegt."""
    if not e["date"]:
        return
    when = f"{short_date(e['date'])}{(' ' + e['time'] + ' Uhr') if e['time'] else ''}"
    if old is None:
        if not e["rsvp"] or e["kind"] == "sitzung":
            return
        title, body = "📅 Neuer Termin", f"{e['title']} am {when} – sag gleich zu oder ab."
    elif e["cancelled"] and not old["cancelled"]:
        title, body = "❌ Termin fällt aus", f"{e['title']} am {short_date(e['date'])} fällt aus."
    elif (e["date"], e["time"]) != (old["date"], old["time"]) and not e["cancelled"]:
        title, body = "🔁 Termin verschoben", f"{e['title']} ist jetzt am {when}."
    else:
        return
    deliver_async(active_member_ids(), title, body, f"/termine/{e['id']}",
                  key=f"evt-{e['id']}-{e['date']}-{e['time']}-{e['cancelled']}-{'neu' if old is None else 'chg'}")


# --- Geplante Erinnerungen ----------------------------------------------------

def run_due():
    """Wird alle paar Minuten aufgerufen. Doppelte Nachrichten verhindert notify_log."""
    db = get_db()
    s = get_settings()
    now = now_local()
    today = now.date()
    if now.hour < int(s.get("notify_uhrzeit") or 10):
        return 0
    sent = 0
    days = int(s.get("notify_tage_vorher") or 3)
    horizon = (today + timedelta(days=days)).isoformat()
    events = db.execute("SELECT * FROM events WHERE rsvp=1 AND cancelled=0 AND date >= ? AND date <= ?",
                        (today.isoformat(), horizon)).fetchall()
    active = active_member_ids()
    for e in events:
        answered = {r["member_id"] for r in db.execute("SELECT member_id FROM event_rsvp WHERE event_id=?", (e["id"],))}
        missing = [m for m in active if m not in answered]
        when = f"{short_date(e['date'])}{(' um ' + e['time'] + ' Uhr') if e['time'] else ''}"
        frist = f" bis {short_date(e['rsvp_until'])}" if e["rsvp_until"] else ""
        if missing:
            sent += deliver(missing, f"📅 {e['title']} am {short_date(e['date'])}",
                            f"Nächstes Treffen {when} – bitte zu- oder absagen{frist}" + ("" if frist else "."),
                            f"/termine/{e['id']}", key=f"rsvp-open-{e['id']}-{e['date']}")
        if e["rsvp_until"] == today.isoformat() and missing:
            sent += deliver(missing, "⏰ Heute ist Rückmeldeschluss",
                            f"{e['title']} {when}: Du hast noch nicht geantwortet – sonst gibt's Strafe 4.",
                            f"/termine/{e['id']}", key=f"rsvp-last-{e['id']}-{e['rsvp_until']}")
    soon = (now + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")
    nowk = now.strftime("%Y-%m-%dT%H:%M")
    for p in db.execute("SELECT * FROM polls WHERE status='offen' AND closes_at IS NOT NULL AND closes_at > ? AND closes_at <= ?",
                        (nowk, soon)).fetchall():
        ids = vorstand_ids() if p["audience"] == "vorstand" else active
        voted = {r["member_id"] for r in db.execute("SELECT member_id FROM poll_voters WHERE poll_id=?", (p["id"],))}
        missing = [m for m in ids if m not in voted]
        if missing:
            sent += deliver(missing, "🗳️ Abstimmung endet bald",
                            f"„{p['title']}“ läuft bis {short_date(p['closes_at'])} {p['closes_at'][11:]} Uhr – deine Stimme fehlt.",
                            f"/abstimmungen/{p['id']}", key=f"poll-end-{p['id']}")
    from .logic import birthdays
    for b in birthdays(0, today):
        m = b["m"]
        name = m["short"] or m["name"]
        age = f" – {b['age']} Jahre" if b["age"] else ""
        sent += deliver([x for x in active if x != m["id"]], f"🎂 {name} hat heute Geburtstag",
                        f"{name} feiert heute{age}. Gratulieren nicht vergessen!", "/mitglieder",
                        key=f"bday-{m['id']}-{today.year}")
    if today.month == 12 and today.day == 31:
        sent += deliver(active, f"🎆 Dein KCU-Jahresrückblick {today.year}",
                        "Strafen, Siege, Sterne & Co. – schau dir an, wie dein Jahr im KCU war!",
                        f"/rueckblick/{today.year}", key=f"wrapped-{today.year}")
    return sent


def start_scheduler(app, interval=300):
    """Ein Hintergrund-Thread für Erinnerungen. Ein Dateischloss sorgt dafür, dass er nur einmal läuft."""
    lock_path = os.path.join(app.config["DATA_DIR"], "scheduler.lock")
    try:
        fh = open(lock_path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False

    def loop():
        time.sleep(20)
        while True:
            try:
                with app.app_context():
                    run_due()
            except Exception:  # noqa: BLE001
                log.exception("Erinnerungslauf fehlgeschlagen")
            time.sleep(interval)

    t = threading.Thread(target=loop, name="kcu-scheduler", daemon=True)
    t._lock_handle = fh  # Schloss offen halten
    t.start()
    return True
