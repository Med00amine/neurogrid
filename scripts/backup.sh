#!/usr/bin/env bash
# Full backup: the PostgreSQL database (custom format) + the uploaded files. Output goes to ./backups (git-ignored).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
U="${POSTGRES_USER:-neurogrid}"; D="${POSTGRES_DB:-neurogrid}"
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p backups && chmod 700 backups
docker compose exec -T db pg_dump -U "$U" -d "$D" -Fc > "backups/neurogrid-$TS.dump"
if docker compose ps --status running api >/dev/null 2>&1; then
  docker compose exec -T api tar -C /data -cf - uploads 2>/dev/null > "backups/uploads-$TS.tar" || true
fi
chmod 600 backups/*-"$TS".*
echo "Backup written:"; ls -lh backups/*-"$TS".*
echo "Contains personal data. Keep it encrypted and off GitHub."
