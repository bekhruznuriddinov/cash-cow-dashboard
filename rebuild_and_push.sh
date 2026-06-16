#!/bin/bash
# Runs after market close — rebuilds data.json and pushes to GitHub Pages.
set -e

DASHBOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DASHBOARD_DIR"

python3 build.py

git add data.json
git diff --cached --quiet && echo "No changes to push." && exit 0

git commit -m "data: $(date '+%Y-%m-%d') close"
git push origin main
echo "Dashboard updated."
