#!/bin/sh
# Checks the whole setup and prints only names and OK/MISSING, never a value.
#
#   scripts/check_setup.sh            local .env, API key, mail, GitHub secrets
#   scripts/check_setup.sh --no-mail  the same, without sending a test email
#
# Upload the secrets first, yourself:  gh secret set -f .env -R yadgire7/mandi-prices
set -u

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GH_REPO="${GH_REPO:-yadgire7/mandi-prices}"
PY="$REPO_DIR/.venv/bin/python"
NAMES="DATA_GOV_IN_API_KEY SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD MAIL_FROM MAIL_TO"
failed=0
ok()   { echo "  OK       $1"; }
bad()  { echo "  MISSING  $1"; failed=1; }

echo "1. .env on this Mac"
if [ ! -f "$REPO_DIR/.env" ]; then
    bad ".env (copy .env.example)"
else
    case "$(stat -f %Lp "$REPO_DIR/.env" 2>/dev/null || stat -c %a "$REPO_DIR/.env")" in
        600) ok ".env is private (600)" ;;
        *) echo "  WARN     .env should be private: chmod 600 .env" ;;
    esac
    for n in $NAMES; do
        v="$(sed -n "s/^$n=//p" "$REPO_DIR/.env" | tail -n 1)"
        if [ -n "$v" ]; then ok "$n"; else bad "$n"; fi
    done
fi

echo "2. data.gov.in API key works"
if "$PY" - "$REPO_DIR" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from fetch_mandi_prices import HERE, read_dotenv
from mandi.api import Client, load_api_key
from mandi.config import load_config
import os
env = {**read_dotenv(HERE / ".env"), **os.environ}
key, _ = load_api_key(env)
if not key:
    print("  MISSING  DATA_GOV_IN_API_KEY"); sys.exit(1)
cfg = load_config(HERE / "config.yaml")
try:
    data = Client(key).get(cfg["api"]["resource"], {"limit": 1, cfg["api"]["state_filter"]: cfg["state"]})
except Exception as err:  # ApiError messages are already redacted
    print(f"  FAILED   {type(err).__name__}: {err}"); sys.exit(1)
print(f"  OK       key accepted ({data.get('total', '?')} Maharashtra rows in the dataset)")
EOF
then :; else failed=1; fi

echo "3. Mail settings send a test email"
if [ "${1:-}" = "--no-mail" ]; then
    echo "  SKIPPED  (--no-mail)"
elif (cd "$REPO_DIR" && "$PY" fetch_mandi_prices.py --test-email >/dev/null 2>"$REPO_DIR/out/.check-mail.err"); then
    ok "test email sent: check the inbox in MAIL_TO (and spam)"
else
    echo "  FAILED   $(tail -n 1 "$REPO_DIR/out/.check-mail.err")"; failed=1
fi
rm -f "$REPO_DIR/out/.check-mail.err"

echo "4. GitHub secrets in $GH_REPO (names only; GitHub never shows values)"
listed="$(gh secret list -R "$GH_REPO" --json name --jq '.[].name' 2>/dev/null)" || { bad "could not list secrets (gh auth status?)"; listed=""; }
for n in $NAMES; do
    if printf '%s\n' "$listed" | grep -qx "$n"; then ok "$n"; else bad "$n"; fi
done

echo
if [ "$failed" = 0 ]; then echo "All set."; else echo "Fix the lines marked MISSING or FAILED, then run this again."; fi
exit "$failed"
