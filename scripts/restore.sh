#!/usr/bin/env bash
# Restore a backup made by backup.sh. REPLACES the current database. Usage: scripts/restore.sh backups/neurogrid-XXXX.dump
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:?usage: scripts/restore.sh backups/neurogrid-<timestamp>.dump}"
[ -f "$FILE" ] || { echo "no such file: $FILE"; exit 1; }
[ -f .env ] && set -a && . ./.env && set +a
U="${POSTGRES_USER:-neurogrid}"; D="${POSTGRES_DB:-neurogrid}"
echo "This will REPLACE the database '$D' with $FILE."
echo
echo "WARNING: anyone who unsubscribed, bounced or was suppressed AFTER this backup was made will be forgotten."
echo "Keep live sending OFF (LIVE_SENDING_ENABLED=false) until you have re-checked the suppression list;"
echo "re-reading your mailbox (Replies -> Check mailbox now) re-applies unsubscribes received since the backup."
echo
read -r -p "Type RESTORE to continue: " ANSWER
[ "$ANSWER" = "RESTORE" ] || { echo "cancelled"; exit 1; }
docker compose stop api worker >/dev/null 2>&1 || true
docker compose exec -T db psql -U "$U" -d postgres -c "DROP DATABASE IF EXISTS \"$D\" WITH (FORCE)" -c "CREATE DATABASE \"$D\""
docker compose exec -T db pg_restore -U "$U" -d "$D" --no-owner --exit-on-error < "$FILE"
UP="${FILE/neurogrid-/uploads-}"; UP="${UP%.dump}.tar"
if [ -f "$UP" ]; then docker compose start api >/dev/null && docker compose exec -T api tar -C /data -xf - < "$UP" && echo "uploads restored"; fi
docker compose up -d api worker
echo "Restored. Check http://localhost:${HOST_PORT:-8000}/api/health/ready"
echo "NEXT: Replies -> Check mailbox now, then review Settings -> Privacy & compliance -> Suppression list BEFORE any live send."
