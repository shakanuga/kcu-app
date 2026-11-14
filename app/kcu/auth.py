"""Anmeldung über Cloudflare Access und Rechte.

Produktiv (CF_TEAM_DOMAIN + CF_ACCESS_AUD gesetzt):
  Jede Anfrage muss ein gültiges, von Cloudflare signiertes JWT mitbringen.
  Daraus kommt die E-Mail-Adresse, über die das Mitglied erkannt wird.

Testmodus (nur KCU_DEV_EMAIL gesetzt):
  Für den lokalen Test im Heimnetz – jeder Aufruf gilt als diese E-Mail.
  Auf jeder Seite erscheint ein roter Hinweis. Niemals über den Tunnel nutzen!

Rollen (mehrere pro Person möglich):
  admin      – Verwaltung: Mitglieder, Rollen, Strafenkatalog, Termine, Einstellungen
  vorstand   – Vorstandsbereich, Kasse, Redaktion, Projekte anlegen
  kassenwart – Kasse (Zahlungen, Kassenbuch), Projekte anlegen
  redaktion  – Protokolle bearbeiten/löschen/importieren, Museum, Satzung
               (neue Protokolle hochladen darf jedes Mitglied)
  Dazu kommt die zeitweise Kassenwart-Vertretung (Einstellung kasse_vertretung_*).
"""
import os
from datetime import date

import jwt
from flask import g, request, session

from .db import get_db

ROLES = ["admin", "vorstand", "kassenwart", "redaktion"]
ROLE_LABEL = {"admin": "Admin", "vorstand": "Vorstand", "kassenwart": "Kassenwart", "redaktion": "Redaktion",
              "mitglied": "Mitglied"}

_jwk_client = None


def cf_mode():
    return bool(os.environ.get("CF_TEAM_DOMAIN") and os.environ.get("CF_ACCESS_AUD"))


def dev_mode():
    return not cf_mode() and bool(os.environ.get("KCU_DEV_EMAIL"))


def _team_url():
    team = os.environ["CF_TEAM_DOMAIN"].strip().rstrip("/")
    if not team.startswith("https://"):
        team = "https://" + team
    return team


def _verify_cf_token(token):
    global _jwk_client
    team = _team_url()
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(f"{team}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)
    key = _jwk_client.get_signing_key_from_jwt(token)
    claims = jwt.decode(token, key.key, algorithms=["RS256"],
                        audience=os.environ["CF_ACCESS_AUD"].strip(), issuer=team)
    return claims.get("email")


def resolve_email():
    if cf_mode():
        token = request.headers.get("Cf-Access-Jwt-Assertion") or request.cookies.get("CF_Authorization")
        if not token:
            return None
        try:
            return _verify_cf_token(token)
        except Exception:
            return None
    if dev_mode():
        return os.environ.get("KCU_DEV_EMAIL")
    return None


def admin_emails():
    raw = os.environ.get("KCU_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def roles_of(member_id):
    if member_id is None:
        return set()
    return {r["role"] for r in get_db().execute("SELECT role FROM member_roles WHERE member_id=?", (member_id,))}


def set_roles(member_id, roles):
    """Rollen eines Mitglieds setzen – bestehende behalten ihr „seit“-Datum."""
    from datetime import date
    db = get_db()
    cur = roles_of(member_id)
    for r in cur - set(roles):
        db.execute("DELETE FROM member_roles WHERE member_id=? AND role=?", (member_id, r))
    for r in set(roles) - cur:
        db.execute("INSERT OR IGNORE INTO member_roles(member_id, role, since) VALUES (?,?,?)",
                   (member_id, r, date.today().isoformat()))


def role_since():
    """{(member_id, role): 'YYYY-MM-DD'}"""
    return {(r["member_id"], r["role"]): r["since"] for r in get_db().execute("SELECT * FROM member_roles")}


def roles_map():
    out = {}
    for r in get_db().execute("SELECT member_id, role FROM member_roles"):
        out.setdefault(r["member_id"], set()).add(r["role"])
    return out


def load_user():
    """Setzt g.user (dict mit 'roles') oder None."""
    g.user = None
    email = resolve_email()
    g.email = email
    if not email:
        return
    row = get_db().execute("SELECT * FROM members WHERE lower(email)=lower(?)", (email,)).fetchone()
    is_env_admin = email.lower() in admin_emails()
    # Testmodus: als anderes Mitglied ansehen (nur lokal, nie mit Cloudflare Access)
    if dev_mode() and session.get("dev_as"):
        other = get_db().execute("SELECT * FROM members WHERE id=?", (session["dev_as"],)).fetchone()
        if other:
            row = other
            is_env_admin = bool(other["email"]) and other["email"].lower() in admin_emails()
            g.email = other["email"] or ""
        else:
            session.pop("dev_as", None)
    if row:
        user = dict(row)
        user["roles"] = roles_of(row["id"])
    elif is_env_admin:
        user = {"id": None, "name": "Admin", "short": "Admin", "email": email, "status": "aktiv",
                "number": "", "roles": set()}
    else:
        return
    if is_env_admin:
        user["roles"].add("admin")
    g.user = user


def has_role(role):
    u = g.get("user")
    return bool(u) and role in u.get("roles", set())


def is_kasse_vertretung():
    u = g.get("user")
    s = g.get("settings", {})
    if not u or not u.get("id"):
        return False
    vid, bis = s.get("kasse_vertretung_id") or "", s.get("kasse_vertretung_bis") or ""
    return vid == str(u["id"]) and bool(bis) and date.today().isoformat() <= bis


def can(perm):
    """Zentrale Rechteprüfung."""
    if not g.get("user"):
        return False
    if perm == "admin":
        return has_role("admin")
    if perm == "vorstand":
        return has_role("vorstand")
    if perm == "kasse":           # Kosten, Zahlungen, Kassenbuch, Strafen löschen
        return has_role("kassenwart") or has_role("vorstand") or has_role("admin") or is_kasse_vertretung()
    if perm == "redaktion":       # Protokolle bearbeiten/löschen, Museum, Satzung
        return has_role("redaktion") or has_role("vorstand") or has_role("admin")
    if perm == "projekt_anlegen":
        return has_role("admin") or has_role("vorstand") or has_role("kassenwart")
    if perm == "termine":         # Termine anlegen/verschieben/absagen
        return has_role("admin") or has_role("vorstand")
    if perm == "rollen":          # Rollen übergeben und Wahlen starten
        return has_role("admin") or has_role("vorstand")
    if perm == "kicker_verwalten":  # Turniere importieren/löschen, Spielernamen zuordnen
        return has_role("admin") or has_role("vorstand")
    return False
