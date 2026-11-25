#!/bin/sh
# The laptop's job while GitHub cannot reach data.gov.in (it refuses servers
# outside India): fetch the prices at 05:45, 11:45, 17:45 and 23:45, and push
# them to the `data` branch. GitHub's scheduled run at 06:00, 12:00, 18:00 and
# 00:00 does everything else: checks, pages, snapshot and the email.
#
#   scripts/fetcher.sh install     start the schedule (replaces the shadow-run one)
#   scripts/fetcher.sh uninstall   stop it and remove it
#   scripts/fetcher.sh status      is it loaded, and what was last pushed
#   scripts/fetcher.sh run         fetch and push once, now
#
# If the Mac is asleep at a fetch time, launchd fetches once on wake. If it is
# off, nothing is pushed and GitHub's email says the data is old.
set -eu

LABEL="local.mandi-prices.fetcher"
OLD_LABEL="local.mandi-prices.shadow"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
FILE="$REPO/out/fetched/prices.json.gz"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"  # launchd starts with a bare PATH

push() {
    # Build a one-file commit without touching the working tree, and replace
    # the branch with it: only the latest data is kept, so the repo never grows.
    cd "$REPO"
    blob="$(git hash-object -w "$FILE")"
    tree="$(printf '100644 blob %s\tprices.json.gz\n' "$blob" | git mktree)"
    commit="$(git commit-tree "$tree" -m "$1")"
    git push --quiet --force origin "$commit:refs/heads/data"
}

case "${1:-}" in
run)
    echo "== $(date '+%Y-%m-%d %H:%M') fetch"
    if (cd "$REPO" && "$PY" fetch_mandi_prices.py --fetch-only "$FILE"); then
        msg="Fetched $(date '+%Y-%m-%d %H:%M') IST"
    else
        # The error is saved in the file, so GitHub's email can say what went wrong.
        msg="Fetch failed $(date '+%Y-%m-%d %H:%M') IST"
    fi
    [ -f "$FILE" ] || { echo "No file to push"; exit 1; }
    push "$msg" && echo "Pushed: $msg"
    ;;
install)
    [ -x "$PY" ] || { echo "No virtualenv: python3 -m venv .venv && ./.venv/bin/pip install --require-hashes -r requirements.txt"; exit 1; }
    launchctl bootout "$DOMAIN/$OLD_LABEL" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$OLD_LABEL.plist"
    mkdir -p "$REPO/out" "$HOME/Library/LaunchAgents"
    {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0"><dict>'
        echo "  <key>Label</key><string>$LABEL</string>"
        echo "  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$REPO/scripts/fetcher.sh</string><string>run</string></array>"
        echo "  <key>WorkingDirectory</key><string>$REPO</string>"
        echo '  <key>StartCalendarInterval</key><array>'
        for hour in 5 11 17 23; do
            echo "    <dict><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>45</integer></dict>"
        done
        echo '  </array>'
        echo "  <key>StandardOutPath</key><string>$REPO/out/fetcher.log</string>"
        echo "  <key>StandardErrorPath</key><string>$REPO/out/fetcher.log</string>"
        echo '</dict></plist>'
    } > "$PLIST"
    chmod 644 "$PLIST"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed: fetches at 05:45, 11:45, 17:45 and 23:45 (this Mac must be on IST)."
    echo "The old shadow-run schedule is removed. Log: out/fetcher.log"
    ;;
uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed. Nothing else was changed."
    ;;
status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
        echo "Schedule: loaded"
        launchctl print "$DOMAIN/$LABEL" | grep -E "runs =|last exit code" || true
    else
        echo "Schedule: not installed"
    fi
    echo "Last push to the data branch:"
    (cd "$REPO" && git ls-remote --exit-code origin refs/heads/data >/dev/null 2>&1 \
        && git fetch --quiet origin data && git log -1 --format='  %s' FETCH_HEAD) || echo "  none yet"
    [ -f "$REPO/out/fetcher.log" ] && { echo "Log:"; tail -n 4 "$REPO/out/fetcher.log" | sed 's/^/  /'; }
    exit 0
    ;;
*)
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
