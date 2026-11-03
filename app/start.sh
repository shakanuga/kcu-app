#!/bin/sh
# Startskript der KCU-App.
# Beim ersten Start (und wenn sich requirements.txt ändert) werden die
# Python-Pakete in /data/venv installiert. Danach startet die App.
set -e

export HOME=/data/home
mkdir -p "$HOME" /data

VENV=/data/venv
REQ=/app/requirements.txt

if [ ! -x "$VENV/bin/gunicorn" ] || ! cmp -s "$REQ" "$VENV/.requirements"; then
  echo "[KCU] Installiere Python-Pakete (einmalig, dauert ca. 1 Minute) ..."
  python -m venv "$VENV"
  "$VENV/bin/pip" install --no-cache-dir --upgrade pip >/dev/null
  "$VENV/bin/pip" install --no-cache-dir -r "$REQ"
  cp "$REQ" "$VENV/.requirements"
fi

cd /app
echo "[KCU] Starte App auf Port 8000 ..."
exec "$VENV/bin/gunicorn" --preload --workers 2 --threads 4 --bind 0.0.0.0:8000 \
  --access-logfile - --forwarded-allow-ips "*" "kcu:create_app()"
