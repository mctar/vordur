# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Vordur ("the watchman") is a single-file Python CLI that monitors changelog pages for updates. It fetches pages, extracts entries via CSS selectors, diffs against stored state, appends new entries to monthly HTML diary files, and sends email alerts via SMTP.

## Commands

```bash
pip install -r requirements.txt          # Install deps (requests, beautifulsoup4, pyyaml)
python vordur.py                         # Check all sources
python vordur.py --dry-run               # Check without writing diary or sending emails
python vordur.py --source "Claude Code"  # Check a single source
python vordur.py --list                  # Show all sources and their status
python vordur.py --test-email            # Verify SMTP config
python vordur.py --reset "Source Name"   # Clear state for a source (re-detect on next run)
```

No test suite or linter is configured.

## Architecture

Everything lives in `vordur.py`. The flow for each source is:

1. **Fetch** the URL (`requests.get`)
2. **Extract** entries using the CSS selector (`BeautifulSoup.select`), hash each entry's text with SHA-256
3. **Diff** entry hashes against `state/<source>.json` to find new ones
4. **Diary** — append new entries to `output/YYYY-MM.html` (inserted after `<!-- ENTRIES -->` marker, newest first), then regenerate `output/index.html`
5. **Email** — send a plain-text alert via SMTP if new entries exist

State files (`state/*.json`) store `known_hashes`, `last_check`, and `last_update`. They are keyed by source name (lowercased, spaces/slashes replaced with hyphens).

## Config

`config.yaml` defines three sections:
- `sources` — list of `{name, url, selector, max_entries?}` entries
- `smtp` — SMTP credentials for email alerts
- `output` — `diary_dir`, `state_dir`, `base_url`

SMTP password is loaded from the `SMTP_PASSWORD` env var (set in `.env`, which is gitignored). Do not commit secrets or log them.

## Deployment

Intended to run via cron (every 30 min). The `output/` directory is static HTML served by a web server or Cloudflare Tunnel.
