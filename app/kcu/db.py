"""Datenbank (SQLite): Schema, Migrationen und Startdaten (aus seed.json im Data-Ordner)."""
import json
import os
import shutil
import sqlite3
from datetime import date

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS members (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  name     TEXT NOT NULL,
  short    TEXT,
  number   TEXT,
  email    TEXT,
  role     TEXT NOT NULL DEFAULT 'mitglied',   -- mitglied | redaktion | kassenwart | admin
  status   TEXT NOT NULL DEFAULT 'aktiv',      -- aktiv | ehren | ehemalig
  aufgabe  TEXT,
  joined   TEXT,                               -- YYYY-MM (Beitrag ab)
  left_at  TEXT,                               -- YYYY-MM (Beitrag bis)
  opening  REAL NOT NULL DEFAULT 0,            -- Startsaldo: + Schulden / - Guthaben
  sort     INTEGER NOT NULL DEFAULT 100
);
CREATE UNIQUE INDEX IF NOT EXISTS members_email ON members(lower(email)) WHERE email IS NOT NULL AND email <> '';

CREATE TABLE IF NOT EXISTS catalog (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL,                   -- strafe | schnell
  code        TEXT,
  title       TEXT NOT NULL,
  description TEXT,
  amount      REAL NOT NULL DEFAULT 0,
  natural     TEXT,                            -- Naturalstrafe, z. B. "1 Kasten"
  active      INTEGER NOT NULL DEFAULT 1,
  sort        INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS penalties (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id   INTEGER NOT NULL REFERENCES members(id),
  catalog_id  INTEGER REFERENCES catalog(id),
  kind        TEXT,
  title       TEXT NOT NULL,
  amount      REAL NOT NULL DEFAULT 0,
  natural     TEXT,
  date        TEXT NOT NULL,
  note        TEXT,
  created_by  TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS payments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id   INTEGER NOT NULL REFERENCES members(id),
  date        TEXT NOT NULL,
  amount      REAL NOT NULL,
  note        TEXT,
  created_by  TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ledger (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  date        TEXT NOT NULL,
  kind        TEXT NOT NULL,                   -- einnahme | ausgabe
  amount      REAL NOT NULL,
  purpose     TEXT NOT NULL,
  created_by  TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS protocols (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  date        TEXT NOT NULL,
  title       TEXT NOT NULL,
  body        TEXT,
  file        TEXT,
  file_name   TEXT,
  created_by  TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS merch (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  year        INTEGER,
  category    TEXT,
  description TEXT,
  status      TEXT NOT NULL DEFAULT 'vorhanden', -- vorhanden | weg | einzelstueck
  created_by  TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS merch_owners (            -- wer hat ein Exemplar (Sammelbestellung)
  merch_id  INTEGER NOT NULL REFERENCES merch(id) ON DELETE CASCADE,
  member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  qty       INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (merch_id, member_id)
);

CREATE TABLE IF NOT EXISTS merch_photos (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  merch_id  INTEGER NOT NULL REFERENCES merch(id) ON DELETE CASCADE,
  file      TEXT NOT NULL,
  sort      INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS projects (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL,
  description  TEXT,
  budget       REAL,
  status       TEXT NOT NULL DEFAULT 'geheim',   -- geheim | freigegeben
  created_by   TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  released_at  TEXT,
  released_by  TEXT
);

CREATE TABLE IF NOT EXISTS project_members (
  project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  member_id   INTEGER NOT NULL REFERENCES members(id),
  PRIMARY KEY (project_id, member_id)
);

CREATE TABLE IF NOT EXISTS member_roles (
  member_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  role       TEXT NOT NULL,                        -- admin | vorstand | kassenwart | redaktion
  PRIMARY KEY (member_id, role)
);

CREATE TABLE IF NOT EXISTS event_rsvp (
  event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  member_id   INTEGER NOT NULL REFERENCES members(id),
  status      TEXT NOT NULL,                       -- ja | nein | vielleicht
  reason      TEXT,
  guests      TEXT,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (event_id, member_id)
);

CREATE TABLE IF NOT EXISTS polls (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  title         TEXT NOT NULL,
  description   TEXT,
  kind          TEXT NOT NULL DEFAULT 'single',    -- single | multi
  audience      TEXT NOT NULL DEFAULT 'alle',      -- alle | vorstand
  anonymous     INTEGER NOT NULL DEFAULT 0,
  show_results  TEXT NOT NULL DEFAULT 'nach_abstimmung',  -- sofort | nach_abstimmung | nach_ende
  closes_at     TEXT,                              -- YYYY-MM-DDTHH:MM
  status        TEXT NOT NULL DEFAULT 'offen',     -- offen | geschlossen
  event_id      INTEGER REFERENCES events(id) ON DELETE SET NULL,
  created_by_id INTEGER,
  created_by    TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS poll_options (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  poll_id  INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
  label    TEXT NOT NULL,
  sort     INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS poll_votes (           -- bei anonymen Abstimmungen ohne member_id
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  poll_id    INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
  option_id  INTEGER NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
  member_id  INTEGER
);

CREATE TABLE IF NOT EXISTS poll_voters (          -- wer hat schon abgestimmt
  poll_id    INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
  member_id  INTEGER NOT NULL,
  voted_at   TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (poll_id, member_id)
);

CREATE TABLE IF NOT EXISTS vorstand_notes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  title       TEXT NOT NULL,
  body        TEXT,
  pinned      INTEGER NOT NULL DEFAULT 0,
  created_by  TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  title  TEXT NOT NULL,
  date   TEXT,          -- YYYY-MM-DD, optional
  label  TEXT,          -- freier Text, z. B. "16.–18.10.2026" oder "November/Dezember"
  note   TEXT,
  sort   INTEGER NOT NULL DEFAULT 100
);

-- ---------- Kicker: Turniere (Import aus Kickertool und eigene) ----------
CREATE TABLE IF NOT EXISTS kt_tournaments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ext_id      TEXT UNIQUE,                     -- Kickertool-ID (Import) – verhindert Doppelimporte
  name        TEXT NOT NULL,
  date        TEXT NOT NULL,                   -- YYYY-MM-DD
  mode        TEXT NOT NULL,                   -- monster_dyp | lms | round_robin | whist | swiss | elimination ...
  name_type   TEXT,                            -- Kickertool: monster_dyp | dyp | single | team
  discipline  TEXT NOT NULL DEFAULT 'kicker',  -- kicker | bierpong | flunky | sonstiges
  source      TEXT NOT NULL DEFAULT 'import',  -- import | live
  status      TEXT NOT NULL DEFAULT 'fertig',  -- laeuft | fertig
  options     TEXT,                            -- JSON (Ziel-Tore, Leben, Teamgröße …)
  created_by  TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kt_matches (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tournament_id INTEGER NOT NULL REFERENCES kt_tournaments(id) ON DELETE CASCADE,
  stage         TEXT NOT NULL DEFAULT 'gruppe',  -- gruppe | ko
  round_no      INTEGER NOT NULL DEFAULT 1,
  round_label   TEXT,
  seq           INTEGER NOT NULL DEFAULT 0,
  team1         TEXT NOT NULL,                   -- JSON-Liste der Spielernamen
  team2         TEXT NOT NULL,
  score1        INTEGER,
  score2        INTEGER,
  status        TEXT NOT NULL DEFAULT 'fertig',  -- offen | fertig
  played_at     TEXT
);
CREATE INDEX IF NOT EXISTS kt_matches_t ON kt_matches(tournament_id);

CREATE TABLE IF NOT EXISTS kt_standings (            -- Endplatzierungen (aus dem Import oder berechnet)
  tournament_id INTEGER NOT NULL REFERENCES kt_tournaments(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,                   -- Teilnehmer (Spieler oder Teamname)
  players       TEXT,                            -- JSON-Liste, wenn Team
  place         INTEGER,
  PRIMARY KEY (tournament_id, name)
);

CREATE TABLE IF NOT EXISTS kt_participants (         -- Teilnehmer eigener Turniere
  tournament_id INTEGER NOT NULL REFERENCES kt_tournaments(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  team          TEXT,                            -- bei festen Teams: Teamname
  lives         INTEGER,
  out_round     INTEGER,
  sort          INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (tournament_id, name)
);

CREATE TABLE IF NOT EXISTS player_aliases (          -- Spielername im Kickertool -> Mitglied
  alias      TEXT PRIMARY KEY,                     -- klein geschrieben
  member_id  INTEGER REFERENCES members(id) ON DELETE CASCADE,
  ignore     INTEGER NOT NULL DEFAULT 0            -- 1 = Gast / nicht zuordnen
);

-- ---------- Benachrichtigungen ----------
CREATE TABLE IF NOT EXISTS push_subs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  endpoint   TEXT NOT NULL UNIQUE,
  p256dh     TEXT NOT NULL,
  auth       TEXT NOT NULL,
  label      TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notify_prefs (
  member_id  INTEGER PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
  push_on    INTEGER NOT NULL DEFAULT 1,
  email_on   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notify_log (
  key        TEXT PRIMARY KEY,                     -- verhindert doppelte Erinnerungen
  sent_at    TEXT NOT NULL DEFAULT (datetime('now')),
  info       TEXT
);
"""

# --- Startdaten ---------------------------------------------------------------
# Vereinsdaten (Mitglieder, Strafenkatalog, Spitznamen …) stehen NICHT im Code, sondern in
# einer seed.json im Data-Ordner. Sie wird nur beim allerersten Start einer leeren Datenbank
# gelesen. Mit KCU_DEMO=1 startet die App ohne eigene Datei mit erfundenen Beispieldaten.

def load_seed(data_dir, app_root):
    path = os.environ.get("KCU_SEED_FILE") or os.path.join(data_dir, "seed.json")
    if not os.path.exists(path) and os.environ.get("KCU_DEMO") == "1":
        path = os.path.join(app_root, "seed", "seed.example.json")
    if not os.path.exists(path):
        return {}, None
    with open(path, encoding="utf-8") as f:
        return json.load(f), os.path.dirname(path)


def default_settings():
    today = date.today()
    return {
        "club_name": "Kicker Club United",
        "club_short": "KCU",
        "founded": "2017",
        "tagline": "Zusammenhalt. Stärke. Mut. Und Bier.",
        "beitrag": "10",
        "kasse_start": f"{today.year:04d}-{today.month:02d}",
        "kasse_startbestand": "0",
        "session_time": "19:30",
        "session_override": "",
        "ki_url": "",
        "instagram_url": "",
        "stammbier": "",
        "satzung_file": "",
        "kasse_vertretung_id": "",
        "kasse_vertretung_bis": "",
        "rsvp_tage_vorher": "1",
        "satzung_label": "",
        "kickertool_url": "https://app.kickertool.de/tournaments",
        "kicker_ziel": "10",
        "kicker_timer": "2",
        "notify_tage_vorher": "3",
        "notify_mode": "admins",
        "rsvp_sperre": "0",
        "impressum": "",
        "notify_uhrzeit": "10",
        "app_url": "",
        "smtp_host": "",
        "smtp_port": "587",
        "smtp_user": "",
        "smtp_pass": "",
        "smtp_from": "",
        "smtp_security": "starttls",
    }


def get_db():
    if "db" not in g:
        path = os.path.join(current_app.config["DATA_DIR"], "kcu.db")
        conn = sqlite3.connect(path, detect_types=0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(app):
    data_dir = app.config["DATA_DIR"]
    os.makedirs(data_dir, exist_ok=True)
    for sub in ("uploads", "thumbs"):
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)

    conn = sqlite3.connect(os.path.join(data_dir, "kcu.db"))
    conn.executescript(SCHEMA)

    # Migrationen für ältere Datenbanken
    ledger_cols = {r[1] for r in conn.execute("PRAGMA table_info(ledger)")}
    if "project_id" not in ledger_cols:
        conn.execute("ALTER TABLE ledger ADD COLUMN project_id INTEGER REFERENCES projects(id)")
    member_cols = {r[1] for r in conn.execute("PRAGMA table_info(members)")}
    if "birthday" not in member_cols:
        conn.execute("ALTER TABLE members ADD COLUMN birthday TEXT")   # YYYY-MM-DD (Jahr optional: 1900 = unbekannt)
    for col in ("avatar", "bio", "position"):
        if col not in member_cols:
            conn.execute(f"ALTER TABLE members ADD COLUMN {col} TEXT")   # Profil: Bild, Spruch, Lieblingsposition
    if "cal_token" not in member_cols:
        conn.execute("ALTER TABLE members ADD COLUMN cal_token TEXT")  # geheimer Schlüssel für das Kalender-Abo
    pay_cols = {r[1] for r in conn.execute("PRAGMA table_info(payments)")}
    if "method" not in pay_cols:
        conn.execute("ALTER TABLE payments ADD COLUMN method TEXT")   # bar | paypal | ueberweisung | verrechnet
        # Zahlungsart aus bisherigen Notizen übernehmen (z. B. aus dem Listen-Import)
        conn.execute("UPDATE payments SET method='paypal' WHERE lower(note) LIKE '%paypal%'")
        conn.execute("UPDATE payments SET method='bar' WHERE method IS NULL AND lower(note) LIKE '%bar%' AND lower(note) NOT LIKE '%bezahlt%'")
        conn.execute("UPDATE payments SET method='verrechnet' WHERE method IS NULL AND lower(note) LIKE '%verrechnet%'")
    if "period" not in pay_cols:
        conn.execute("ALTER TABLE payments ADD COLUMN period TEXT")   # YYYY-MM: für welchen Monat (optional)
        # Aus Notizen wie „Beitrag Juli – …“ (Listen-Import) den Monat übernehmen
        months = ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september",
                  "oktober", "november", "dezember"]
        for pid, note, d in conn.execute("SELECT id, note, date FROM payments WHERE note LIKE 'Beitrag %'").fetchall():
            word = (note or "").split(" ")[1].strip(" –-,").lower() if len((note or "").split(" ")) > 1 else ""
            if word in months and d:
                conn.execute("UPDATE payments SET period=? WHERE id=?", (f"{d[:4]}-{months.index(word) + 1:02d}", pid))
    proto_cols = {r[1] for r in conn.execute("PRAGMA table_info(protocols)")}
    if "created_by_id" not in proto_cols:   # wer hochgeladen hat – darf bis edit_until selbst nachbessern
        conn.execute("ALTER TABLE protocols ADD COLUMN created_by_id INTEGER REFERENCES members(id) ON DELETE SET NULL")
    if "edit_until" not in proto_cols:      # Ortszeit 'YYYY-MM-DD HH:MM:SS'
        conn.execute("ALTER TABLE protocols ADD COLUMN edit_until TEXT")
    if "since" not in {r[1] for r in conn.execute("PRAGMA table_info(member_roles)")}:
        conn.execute("ALTER TABLE member_roles ADD COLUMN since TEXT")   # YYYY-MM-DD: seit wann (für versetzte Wahlen)
    poll_cols = {r[1] for r in conn.execute("PRAGMA table_info(polls)")}
    for col, ddl in [("role", "TEXT"), ("seats", "INTEGER"), ("auto_apply", "INTEGER NOT NULL DEFAULT 0"),
                     ("finalized_at", "TEXT"), ("result", "TEXT"),
                     ("replaces", "TEXT")]:   # JSON: wessen Platz neu besetzt wird (Teilwahl); leer = alle   # für Wahlen (kind='wahl')
        if col not in poll_cols:
            conn.execute(f"ALTER TABLE polls ADD COLUMN {col} {ddl}")
    if "member_id" not in {r[1] for r in conn.execute("PRAGMA table_info(poll_options)")}:
        conn.execute("ALTER TABLE poll_options ADD COLUMN member_id INTEGER REFERENCES members(id) ON DELETE SET NULL")
    merch_cols = {r[1] for r in conn.execute("PRAGMA table_info(merch)")}
    for col, ddl in [("kind", "TEXT NOT NULL DEFAULT 'sammel'"),   # sammel | einzel
                     ("occasion", "TEXT"), ("price", "REAL"),
                     ("holder_id", "INTEGER REFERENCES members(id) ON DELETE SET NULL")]:
        if col not in merch_cols:
            conn.execute(f"ALTER TABLE merch ADD COLUMN {col} {ddl}")
            if col == "kind":
                conn.execute("UPDATE merch SET kind='einzel' WHERE status='einzelstueck'")
    kt_cols = {r[1] for r in conn.execute("PRAGMA table_info(kt_tournaments)")}
    if "guest_token" not in kt_cols:
        conn.execute("ALTER TABLE kt_tournaments ADD COLUMN guest_token TEXT")   # Gast-Link für die TV-Ansicht
    if "counts" not in kt_cols:
        # Wertung: NULL = automatisch (erster Modus des Abends), 1 = zählt immer, 0 = nur zum Spaß
        conn.execute("ALTER TABLE kt_tournaments ADD COLUMN counts INTEGER")
    event_cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    for col, ddl in [("kind", "TEXT NOT NULL DEFAULT 'event'"), ("time", "TEXT"), ("location", "TEXT"),
                     ("rsvp", "INTEGER NOT NULL DEFAULT 0"), ("rsvp_until", "TEXT"),
                     ("cancelled", "INTEGER NOT NULL DEFAULT 0")]:
        if col not in event_cols:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} {ddl}")
    # Einstellungen ergänzen (nur fehlende Schlüssel)
    for k, v in default_settings().items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))

    # Startdaten nur beim allerersten Start (leere Datenbank)
    fresh = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0
    if fresh:
        seed, seed_dir = load_seed(data_dir, app.root_path)
        for k, v in (seed.get("settings") or {}).items():
            conn.execute("UPDATE settings SET value=? WHERE key=?", (str(v), k))
        for i, m in enumerate(seed.get("members") or []):
            mid = conn.execute(
                "INSERT INTO members(name, short, number, status, aufgabe, role, sort) VALUES (?,?,?,?,?, 'mitglied', ?)",
                (m["name"], m.get("short"), m.get("number"), m.get("status", "aktiv"), m.get("aufgabe", ""), i * 10)).lastrowid
            for role in m.get("roles") or []:
                conn.execute("INSERT OR IGNORE INTO member_roles(member_id, role) VALUES (?,?)", (mid, role))
        if conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0] == 0:
            for i, c in enumerate(seed.get("catalog") or []):
                conn.execute(
                    "INSERT INTO catalog(kind, code, title, description, amount, natural, sort) VALUES (?,?,?,?,?,?,?)",
                    (c["kind"], c.get("code"), c["title"], c.get("description", ""), c.get("amount", 0), c.get("natural"), i * 10))
        if conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0:
            for i, e in enumerate(seed.get("events") or []):
                conn.execute("INSERT INTO events(title, date, label, note, sort) VALUES (?,?,?,?,?)",
                             (e["title"], e.get("date"), e.get("label"), e.get("note", ""), i * 10))
        for short, aliases in (seed.get("aliases") or {}).items():
            row = conn.execute("SELECT id FROM members WHERE short=?", (short,)).fetchone()
            if row:
                for a in aliases:
                    conn.execute("INSERT OR IGNORE INTO player_aliases(alias, member_id) VALUES (?,?)", (a.lower(), row[0]))
        for a in seed.get("guests") or []:   # Gastspieler / Füllnamen – zählen nie als Mitglied
            conn.execute("INSERT OR IGNORE INTO player_aliases(alias, member_id, ignore) VALUES (?, NULL, 1)", (a.lower(),))
        pdf = seed.get("satzung_pdf")
        if pdf and seed_dir and os.path.exists(os.path.join(seed_dir, pdf)):
            shutil.copyfile(os.path.join(seed_dir, pdf), os.path.join(data_dir, "uploads", "satzung-seed.pdf"))
            conn.execute("UPDATE settings SET value='satzung-seed.pdf' WHERE key='satzung_file'")
        for flag in ("roles_migrated", "aliases_seeded", "guests_seeded", "aliases_seeded_v2"):
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, '1')", (flag,))

    # Rollen aus v1 (eine Rolle pro Mitglied) einmalig in die Rollentabelle übernehmen
    if not conn.execute("SELECT value FROM settings WHERE key='roles_migrated'").fetchone():
        for mid, role in conn.execute("SELECT id, role FROM members WHERE role <> 'mitglied'").fetchall():
            conn.execute("INSERT OR IGNORE INTO member_roles(member_id, role) VALUES (?,?)", (mid, role))
        conn.execute("INSERT INTO settings(key, value) VALUES ('roles_migrated', '1')")

    conn.commit()
    conn.close()


def get_settings():
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key, value):
    db = get_db()
    db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))
    db.commit()
