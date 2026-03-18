#!/usr/bin/env bash
# Runs vordur, then deploys output/ to gh-pages branch.
# Intended to be called by cron.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv/bin/python"
OUTPUT_DIR="$SCRIPT_DIR/output"
DEPLOY_DIR="/tmp/vordur-ghp"
REPO="git@github.com:mctar/vordur.git"

# Run vordur
"$VENV" "$SCRIPT_DIR/vordur.py" 2>&1

# Deploy to gh-pages
rm -rf "$DEPLOY_DIR"
git clone --branch gh-pages --single-branch --depth 1 "$REPO" "$DEPLOY_DIR" 2>/dev/null

# Copy output files (preserve CNAME)
cp "$OUTPUT_DIR"/* "$DEPLOY_DIR"/

cd "$DEPLOY_DIR"
git add -A
if git diff --cached --quiet; then
    echo "No changes to deploy."
else
    git commit -m "Update diary $(date -u +%Y-%m-%d)"
    git push origin gh-pages
    echo "Deployed."
fi

rm -rf "$DEPLOY_DIR"
