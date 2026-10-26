#!/usr/bin/env bash
# Creates .env with freshly generated secrets. Safe to run once; it refuses to overwrite an existing .env.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && [ "${1:-}" != "--force" ] && { echo ".env already exists. Use --force to replace it (this loses the generated passwords)."; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required to generate secrets"; exit 1; }
read -r -p "Your admin email (used to sign in): " ADMIN_EMAIL
[ -n "$ADMIN_EMAIL" ] || { echo "An email is required"; exit 1; }
gen() { python3 -c "import secrets; print(secrets.token_urlsafe($1))"; }
SECRET_KEY=$(gen 48); PG=$(gen 24); ADMIN_PW=$(gen 18)
umask 077
python3 - "$ADMIN_EMAIL" "$SECRET_KEY" "$PG" "$ADMIN_PW" <<'PY'
import re, sys
email, secret, pg, admin_pw = sys.argv[1:5]
text = open(".env.example").read()
def put(key, value):
    global text
    text = re.sub(rf"^{key}=.*$", lambda _m: f"{key}={value}", text, flags=re.M)
put("SECRET_KEY", secret); put("POSTGRES_PASSWORD", pg); put("ADMIN_EMAIL", email); put("ADMIN_PASSWORD", admin_pw)
put("DATABASE_URL", f"postgresql+psycopg://neurogrid:{pg}@127.0.0.1:55432/neurogrid")
open(".env", "w").write(text)
PY
chmod 600 .env
cat <<MSG

Created .env (readable only by you).

  Sign-in email:     $ADMIN_EMAIL
  Sign-in password:  $ADMIN_PW        <- shown once; also stored in .env until you delete ADMIN_PASSWORD from it

Next:
  docker compose up -d --build
  open http://localhost:8000

Live email stays OFF (LIVE_SENDING_ENABLED=false). Read docs/04-setup-and-operations.md before changing that.
MSG
