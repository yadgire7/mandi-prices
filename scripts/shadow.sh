#!/bin/sh
# Phase 2 shadow runs: run the pipeline on this Mac at 00:00, 06:00, 12:00
# and 18:00 (the same times as the GitHub schedule in Phase 3), using launchd.
#
#   scripts/shadow.sh install     start the schedule
#   scripts/shadow.sh uninstall   stop it and remove it
#   scripts/shadow.sh status      is it loaded, and the last few runs
#   scripts/shadow.sh run         one shadow run now
#
# If the Mac is asleep at a run time, launchd runs it once on wake. If it is
# shut down, that run is skipped. Each run is a real run (API, pages, snapshot,
# email), marked SHADOW so its post is not pasted into the channel.
set -eu

LABEL="local.mandi-prices.shadow"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

case "${1:-}" in
install)
    [ -x "$PY" ] || { echo "No virtualenv: run python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"; exit 1; }
    mkdir -p "$REPO/out" "$HOME/Library/LaunchAgents"
    {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0"><dict>'
        echo "  <key>Label</key><string>$LABEL</string>"
        echo '  <key>ProgramArguments</key><array>'
        echo "    <string>$PY</string><string>$REPO/fetch_mandi_prices.py</string><string>--shadow</string>"
        echo '  </array>'
        echo "  <key>WorkingDirectory</key><string>$REPO</string>"
        echo '  <key>StartCalendarInterval</key><array>'
        for hour in 0 6 12 18; do
            echo "    <dict><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>0</integer></dict>"
        done
        echo '  </array>'
        echo "  <key>StandardOutPath</key><string>$REPO/out/shadow.log</string>"
        echo "  <key>StandardErrorPath</key><string>$REPO/out/shadow.log</string>"
        echo '</dict></plist>'
    } > "$PLIST"
    chmod 644 "$PLIST"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed: runs at 00:00, 06:00, 12:00 and 18:00 (this Mac's time zone must be IST)."
    echo "Output goes to out/shadow.log; one line per run in out/runs.tsv."
    ;;
uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed. Nothing else was changed."
    ;;
status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
        echo "Schedule: loaded ($PLIST)"
        launchctl print "$DOMAIN/$LABEL" | grep -E "last exit code|runs =" || true
    else
        echo "Schedule: not installed"
    fi
    if [ -f "$REPO/out/runs.tsv" ]; then
        echo; tail -n 8 "$REPO/out/runs.tsv" | column -t -s "$(printf '\t')"
    fi
    ;;
run)
    cd "$REPO" && "$PY" fetch_mandi_prices.py --shadow
    ;;
*)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
