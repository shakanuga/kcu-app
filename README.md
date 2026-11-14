# KCU-App – die Vereins-App des Kicker Club United

Eine selbst gehostete Web-App für einen kleinen Verein – gebaut für den **Kicker Club United (gegr. 2017)**,
aber für jeden Stammtisch, Kickerverein oder Freundeskreis mit Kasse nutzbar.
Sie läuft als ein einziger Docker-Container, speichert alles in einer SQLite-Datei und lässt sich auf dem Handy
wie eine App auf den Startbildschirm legen.

- **Technik:** Python 3.12 · Flask · SQLite · Gunicorn · ohne Build-Schritt, ohne externe Datenbank
- **Image:** `ghcr.io/shakanuga/kcu-app:latest` – wird bei jedem Push auf `main` und jeden Sonntag neu gebaut
- **Zugang:** hinter [Cloudflare Access](https://www.cloudflare.com/zero-trust/products/access/) (Anmeldung per E-Mail-PIN),
  zum Ausprobieren auch ganz ohne

> **Datenschutz:** In diesem Repository stehen keine Vereinsdaten. Mitglieder, Strafenkatalog, Satzung usw.
> kommen beim ersten Start aus einer `seed.json`, die nur auf dem eigenen Server liegt (siehe [Startdaten](#startdaten)).

---

## Inhalt

1. [Funktionen](#funktionen)
2. [Rollen & Rechte](#rollen--rechte)
3. [Schnell ausprobieren](#schnell-ausprobieren)
4. [Installation (Docker / Unraid)](#installation-docker--unraid)
5. [Startdaten](#startdaten)
6. [Online gehen mit Cloudflare](#online-gehen-mit-cloudflare)
7. [Alle Variablen](#alle-variablen)
8. [Push, E-Mail & Kalender](#push-e-mail--kalender)
9. [Daten, Backup & Sicherheit](#daten-backup--sicherheit)
10. [Updates](#updates)
11. [Entwickeln](#entwickeln)

---

## Funktionen

### Vereinsleben
| Bereich | Was es kann |
|---|---|
| **Start** | nächste Sitzung mit Countdown und Zu-/Absage, offene Aufgaben, eigener Kontostand |
| **Termine** | monatliche Sitzungen werden automatisch angelegt; Zusage / Vielleicht / Absage mit Grund und Gästen; einstellbare Rückmeldefrist mit Sperre; Strafe für Nicht-Antworter mit einem Klick |
| **Kalender-Abo** | persönlicher Link für iPhone, Android/Google und Outlook – Termine und Geburtstage erscheinen automatisch im Handy-Kalender |
| **Abstimmungen** | Ja/Nein, Terminfindung, Mehrfachauswahl, anonym, mit Enddatum; private Abstimmungen für den Vorstand |
| **Profile** | Profilbild, Rufname, Spitznamen, Lieblingsposition, Spruch, Geburtstag; eigene Kicker-Statistik und Kontostand |
| **Geburtstage** | Erinnerung am Morgen an alle anderen 🎂 |
| **Vorstand** | private Notizen und Abstimmungen – nur mit der Rolle „Vorstand“, auch Admins sehen sie sonst nicht |
| **Projekte** | geheime Planungskassen (z. B. Mannschaftsfahrt), erst nach Freigabe für alle sichtbar |
| **Archiv** | Galerie (Ordner auf dem Server, Lightbox), Merch-Museum, Protokolle (Markdown, Import aus PDF), Satzung (PDF) |
| **Jahresrückblick** | an Silvester für jedes Mitglied – im Stil von „Spotify Wrapped“: Sitzungen, Strafen, Sterne, Traumpartner, Titel des Jahres |

### Strafen & Kasse
| Bereich | Was es kann |
|---|---|
| **Strafen** | Strafenkatalog und Schnellstrafen (z. B. „Ball im Kicker“), per Handy für mehrere Leute auf einmal eintragen |
| **Beiträge** | Monatsbeitrag läuft automatisch auf; Zahlungen mit Zahlungsart (bar, PayPal, Überweisung) und optional „für Monat“ |
| **Kontostand** | pro Mitglied: Startsaldo + Beiträge + Strafen − Zahlungen; Zahlungen tilgen automatisch die ältesten offenen Posten |
| **Jahresübersicht** | Raster mit allen Mitgliedern × Monaten, offene Beträge und Strafen auf einen Blick |
| **Kassenbuch** | Einnahmen und Ausgaben, Kassenbestand, Vertretung für den Kassenwart |
| **Import** | Zahlungen und Strafen als CSV (z. B. aus Excel) mit Vorschau; „Daten zurücksetzen“ mit automatischer Sicherung |

### Kicker
| Bereich | Was es kann |
|---|---|
| **Rangliste** | ★ Sterne (= gewonnene Spiele), Siegquote, Turniersiege, Tordifferenz, Form; Filter nach Jahr und Disziplin (Kicker, Bierpong, Flunkyball) |
| **Wertung** | pro Spielabend zählt nur der **erste Modus** – die Runden danach laufen als „zum Spaß“ und lassen sich zuschalten; auf jeder Turnierseite von Hand änderbar |
| **Spielerprofile** | Traumpartner, Angstgegner, Lieblingsopfer, Serien, Zu-Null-Siege, „Kasten“-Pleiten, Verlauf pro Jahr |
| **Eigene Turniere** | alle Modi wie im [Kickertool](https://kickertool.de): Monster-DYP, Whist, Mit Leben (Last Man Standing), Jeder gegen jeden (einzeln oder Teams), Schweizer System, K.-o. (mit Spiel um Platz 3), Doppel-K.-o. |
| **Turnier-Extras** | Teams zufällig, selbst oder ausgeglichen nach Stärke; Setzliste; Freilose; Turnierbaum; Buchholz-Wertung; Kasten-Strafe direkt aus dem Ergebnis |
| **TV-Ansicht** | Vollbild für Laptop am Fernseher, aktualisiert sich selbst: aktuelles Spiel, nächste Spiele, Tabelle bzw. Turnierbaum; per **Gast-Link** auch ohne Anmeldung (z. B. für einen Smart-TV) |
| **Spielzeit-Timer** | Countdown pro Spiel (z. B. 2 Minuten), startet/pausiert auf allen Handys und dem Fernseher gleichzeitig, mit Signalton |
| **Kickertool** | Import von Exporten (JSON, `.ktool`, auch als ZIP), Export eigener Turniere als JSON; Spitznamen werden Mitgliedern zugeordnet, Gäste zählen nicht |

### Technik
- **Als App installierbar** (PWA) auf Android, iPhone und Desktop, mit Offline-Hinweis
- **Benachrichtigungen** per Web-Push und E-Mail (Termin-Erinnerung, Rückmeldeschluss, neue/endende Abstimmungen, verschobene Termine, Geburtstage); Testbetrieb „nur an Admins“
- **Dunkles Design**, fürs Handy gebaut, mit Tab-Leiste unten

---

## Rollen & Rechte

Eine Person kann mehrere Rollen haben (Admin → Mitglied):

| Rolle | Darf |
|---|---|
| **Mitglied** | alles Öffentliche, Strafen eintragen, zu-/absagen, abstimmen, Turniere starten, Protokolle hochladen (eigene bis zum nächsten Morgen um 6 Uhr selbst bearbeiten/löschen) |
| **Redaktion** | Protokolle bearbeiten, löschen und gesammelt importieren, Museum und Satzung pflegen |
| **Kassenwart** | Zahlungen, Kosten, Strafen löschen, Kassen-Import, Vertretung bestimmen |
| **Vorstand** | Vorstandsbereich, Termine verwalten, Kicker-Turniere verwalten, Kasse wie Kassenwart |
| **Admin** | alles Organisatorische inkl. Kasse – aber **nicht** in geheime Planungsprojekte und den Vorstandsbereich (über `KCU_ADMIN_EMAILS`) |

---

## Schnell ausprobieren

Mit erfundenen Beispieldaten, ohne Anmeldung:

```bash
docker run --rm -p 8000:8000 \
  -e KCU_DEMO=1 -e KCU_DEV_EMAIL=admin@example.org -e KCU_ADMIN_EMAILS=admin@example.org \
  ghcr.io/shakanuga/kcu-app:latest
```

Dann http://localhost:8000 öffnen – man ist als Admin angemeldet. `KCU_DEV_EMAIL` nie auf einem öffentlich
erreichbaren Server setzen.

---

## Installation (Docker / Unraid)

### Docker Compose

```yaml
services:
  kcu:
    image: ghcr.io/shakanuga/kcu-app:latest
    restart: unless-stopped
    ports: ["3012:8000"]
    security_opt: ["no-new-privileges:true"]
    environment:
      KCU_ADMIN_EMAILS: admin@example.org
      # online (siehe „Online gehen“):
      # CF_TEAM_DOMAIN: meinteam.cloudflareaccess.com
      # CF_ACCESS_AUD: <Audience-Tag>
    volumes:
      - ./data:/data              # Datenbank, Uploads, Schlüssel – das ist das Backup
      - ./galerie:/photos:ro      # optional: Fotos, jeder Unterordner wird ein Album
```

Der Container läuft als Benutzer `99:100`. Gehört der Datenordner jemand anderem: `chown -R 99:100 ./data`.

### Unraid

Docker → **Add Container** → **Advanced View**:

| Feld | Wert |
|---|---|
| Name | `kcu` |
| Repository | `ghcr.io/shakanuga/kcu-app:latest` |
| Network Type | eigenes Netz, z. B. `Custom : kicker` (vorher `docker network create kicker`) |
| WebUI | `http://[IP]:[PORT:3012]` |
| Extra Parameters | `--security-opt=no-new-privileges:true` |
| Path `/data` | `/mnt/user/appdata/kcu/data` |
| Path `/photos` | Galerie-Share, **Read Only** |
| Port `8000` | `3012` |
| Variable `KCU_ADMIN_EMAILS` | deine E-Mail-Adresse |

Für automatische Updates eignet sich das Plugin „Auto Update Applications“.

### Erste Schritte in der App
1. **Admin → Mitglieder:** E-Mail-Adressen eintragen – damit melden sich alle an.
2. **Admin → Einstellungen:** Vereinsname, Beitrag, „Kasse rechnet ab“ (Startmonat) und Kassenbestand.
3. Pro Mitglied den **Startsaldo** aus der bisherigen Kasse eintragen (positiv = Schulden, negativ = Guthaben).
4. Optional: alte Kickertool-Turniere unter **Kicker → Importieren** hochladen.

---

## Startdaten

Damit keine Vereinsdaten im Code stehen, liest die App beim **allerersten Start** (leere Datenbank) die Datei
`seed.json` aus dem Data-Ordner, also z. B. `/mnt/user/appdata/kcu/data/seed.json`.
Später wird sie nicht mehr gebraucht. Alles lässt sich auch in der App selbst anlegen.

Aufbau (vollständiges Beispiel: [`app/kcu/seed/seed.example.json`](app/kcu/seed/seed.example.json)):

```json
{
  "settings": { "club_name": "Mein Verein", "beitrag": "10", "instagram_url": "https://…" },
  "members": [
    { "name": "Karl Beispiel", "short": "Kalle", "number": "10", "status": "aktiv",
      "aufgabe": "Vorstand", "roles": ["vorstand"] }
  ],
  "catalog": [
    { "kind": "strafe",  "code": "1",  "title": "Zu spät", "amount": 5.0 },
    { "kind": "schnell", "code": "02", "title": "10:0 verlieren", "amount": 0, "natural": "1 Kasten" }
  ],
  "events":  [ { "title": "Sommerfeier", "date": "2027-08-21", "label": "21.08.2027" } ],
  "aliases": { "Kalle": ["kalli", "karl b"] },
  "guests":  ["gast"],
  "satzung_pdf": "satzung.pdf"
}
```

- `status`: `aktiv`, `ehren` (Ehrenmitglied) oder `ehemalig`
- `roles`: `vorstand`, `kassenwart`, `redaktion`
- `catalog.kind`: `strafe` (Strafenkatalog) oder `schnell` (Schnellstrafe am Kicker, Code `01` = Ball im Kicker, `02` = 10:0)
- `aliases`: Spitznamen aus dem Kickertool → Rufname; `guests`: Namen, die nie als Mitglied zählen
- `satzung_pdf`: Dateiname einer PDF neben der `seed.json`

Ohne `seed.json` startet die App leer, mit `KCU_DEMO=1` mit den Beispieldaten.

---

## Online gehen mit Cloudflare

Im Router wird nichts freigegeben – ein Cloudflare Tunnel baut die Verbindung von innen auf, Cloudflare Access
lässt nur eingetragene E-Mail-Adressen durch (Einmal-PIN per Mail). Für kleine Vereine reicht der kostenlose Plan.

1. **Zero Trust → Access → Anwendungen:** Self-hosted-Anwendung für `kcu.deinedomain.de`,
   Richtlinie „Erlauben“ mit den E-Mail-Adressen der Mitglieder, Anmeldemethode „One-time PIN“.
2. **Tunnel:** Container `cloudflare/cloudflared:latest` im selben Docker-Netz, mit `TUNNEL_TOKEN`;
   Public Hostname `kcu.deinedomain.de` → `http://kcu:8000`.
3. **Öffentliche Ausnahmen:** eine zweite Access-Anwendung mit Richtlinie **„Umgehen / Jeder“** für die Pfade
   `kal` (Kalender-Abo, durch geheimen Link geschützt), `kicker/gast` (Gast-Link für die TV-Ansicht, ebenfalls
   geheimer Link), `static/img` (Logo/App-Symbol), `manifest.webmanifest` (App-Installation) und `impressum`.
4. Im Container `KCU_DEV_EMAIL` entfernen und setzen:
   `CF_TEAM_DOMAIN` = `meinteam.cloudflareaccess.com`, `CF_ACCESS_AUD` = Audience-Tag der Access-Anwendung.
5. Neue Mitglieder: E-Mail in der App **und** in der Access-Richtlinie eintragen.

Die App prüft das Cloudflare-Token zusätzlich selbst; ohne gültige Anmeldung kommt niemand an die Daten.
Die Anmeldeseite lässt sich unter *Wiederverwendbare Komponenten → Benutzerdefinierte Seiten* im Vereinsdesign
gestalten (Logo: `https://kcu.deinedomain.de/static/img/icon-512.png`).

---

## Alle Variablen

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `KCU_ADMIN_EMAILS` | ja | Admin-Adressen, kommagetrennt |
| `CF_TEAM_DOMAIN` | online | z. B. `meinteam.cloudflareaccess.com` |
| `CF_ACCESS_AUD` | online | Audience-Tag der Access-Anwendung |
| `KCU_DEV_EMAIL` | nur Test | meldet ohne Cloudflare als diese Adresse an – **nie online gesetzt lassen** |
| `KCU_DEMO` | – | `1` = beim ersten Start Beispieldaten laden, wenn keine `seed.json` da ist |
| `KCU_SEED_FILE` | – | anderer Pfad zur `seed.json` |
| `KCU_SMTP_HOST`, `_PORT`, `_USER`, `_PASS`, `_FROM`, `_SECURITY` | – | E-Mail-Versand; überschreiben die Felder unter Admin → Einstellungen |
| `KCU_SCHEDULER` | – | `0` schaltet die Erinnerungen im Hintergrund ab |
| `KCU_SECRET` | – | fester Sitzungsschlüssel statt `/data/secret_key` |
| `TZ` | – | Zeitzone, Standard `Europe/Berlin` |

---

## Push, E-Mail & Kalender

- **Push** braucht HTTPS (also die Cloudflare-Adresse), sonst nichts. Die Schlüssel erzeugt die App selbst
  (`/data/vapid_private.pem`). iPhone: erst „Zum Home-Bildschirm“, dann in der App aktivieren.
- **E-Mail:** SMTP unter Admin → Einstellungen eintragen, Passwort besser als Variable `KCU_SMTP_PASS`
  (bei Gmail ein App-Passwort). Es gibt eine Testmail.
- **Testbetrieb:** „Benachrichtigungen an: admins“ schickt automatische Nachrichten nur an Admins – erst auf
  „alle“ stellen, wenn alles passt.
- **Kalender:** Termine → „Kalender abonnieren“. Jeder bekommt einen eigenen, jederzeit erneuerbaren Link.

---

## Daten, Backup & Sicherheit

- Alles Veränderliche liegt in `/data` und **nie im Repo**: Datenbank, Uploads, `secret_key`, `vapid_private.pem`, `seed.json`.
- **Backup = diesen Ordner sichern.** Vor Importen und „Daten zurücksetzen“ legt die App selbst Kopien in `data/backups/` an.
- CSRF-Schutz, Security-Header, Uploads werden neu kodiert (EXIF/GPS entfernt), Push nur an bekannte Dienste.
- Container läuft ohne Root (`99:100`), mit `no-new-privileges`, Galerie nur lesend eingebunden.

---

## Updates

- Unraid → Docker → **Update** (oder automatisch per Plugin). Datenbank-Änderungen spielt die App beim Start selbst ein.
- **Dependabot** schlägt wöchentlich Updates für Python-Pakete, Basis-Image und GitHub Actions vor.
- Das Image wird jeden Sonntag neu gebaut, damit Sicherheitsupdates von Debian und Python enthalten sind.

---

## Entwickeln

```bash
cd app
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
KCU_DATA=../data KCU_PHOTOS=../photos KCU_DEMO=1 \
KCU_DEV_EMAIL=admin@example.org KCU_ADMIN_EMAILS=admin@example.org \
  flask --app "kcu:create_app()" run --debug
```

Dann http://127.0.0.1:5000 öffnen.

Aufbau: `kcu/views.py` (Seiten), `kcu/kicker.py` (Kicker & Turniere), `kcu/kmodes.py` (Turniermodi),
`kcu/kstats.py` (Statistik), `kcu/ktimport.py` (Kickertool-Import), `kcu/notify.py` + `kcu/webpush.py`
(Benachrichtigungen), `kcu/db.py` (Schema, Migrationen, Startdaten).

---

## Lizenz

Der Code steht unter der [MIT-Lizenz](LICENSE). Name und Logo des Kicker Club United gehören dem Verein.
