#!/bin/bash
# Runs after market close — rebuilds data.json and pushes to GitHub Pages.
set -e

DASHBOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(dirname "$DASHBOARD_DIR")"
cd "$DASHBOARD_DIR"

# Load credentials so build.py can pull real balance from Robinhood
if [ -f "$AGENT_DIR/.env" ]; then
    set -a
    source "$AGENT_DIR/.env"
    set +a
fi

python3 build.py

git add data.json balance_history.json
git diff --cached --quiet && echo "No changes to push." && exit 0

git commit -m "data: $(date '+%Y-%m-%d') close"
git push origin main
echo "Dashboard updated."
