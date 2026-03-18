# Vordur

*The watchman.* Monitors changelog pages for updates, logs them to a monthly HTML diary, and sends email alerts when something new appears.

## Setup

```bash
pip install -r requirements.txt
```

Edit `config.yaml`:
- Add your sources (URL + CSS selector for entries)
- Configure SMTP credentials
- Set `output.diary_dir` to wherever your web server serves from
- Set `output.base_url` to the public URL (for links in emails)

## Finding the right CSS selector

Open the changelog page in your browser, inspect the DOM, and find the CSS selector that matches individual entries.

- GitHub releases pages: `.Box-body .d-flex` works for the release list
- Most changelog pages use a repeating element: `.changelog-entry`, `.release`, `article`, etc.
- Start broad (e.g. `article` or `section`) and narrow down
- Run `--dry-run` after adding a source to verify extraction works

## Usage

```bash
# Check all sources
python vordur.py

# Dry run (no emails, no diary writes)
python vordur.py --dry-run

# Check a single source
python vordur.py --source "Ollama Releases"

# List all sources and their status
python vordur.py --list

# Test email config
python vordur.py --test-email

# Reset a source (will re-detect everything on next run)
python vordur.py --reset "Ollama Releases"
```

## Cron

Run every 30 minutes:

```
*/30 * * * * cd /path/to/vordur && /usr/bin/python3 vordur.py >> /var/log/vordur.log 2>&1
```

## Serving the diary

The `output/` directory contains static HTML. Point your web server at it, or serve through Cloudflare Tunnel:

```yaml
# cloudflared config
ingress:
  - hostname: btrbot.com
    path: /vordur
    service: http://localhost:8080
```

Then set `output.base_url` in config.yaml to `https://btrbot.com/vordur`.

## File structure

```
vordur/
  config.yaml          # Sources and settings (edit this)
  vordur.py            # The watchman (run this)
  requirements.txt     # Python deps
  state/               # Per-source state (auto-managed)
    ollama-releases.json
  output/              # HTML diary (serve this)
    index.html
    2026-03.html
```

## Adding a new source

1. Open the changelog page, inspect the DOM
2. Find a CSS selector for entries
3. Add to `config.yaml`:
   ```yaml
   - name: "New Tool"
     url: "https://newtool.dev/changelog"
     selector: ".changelog-item"
   ```
4. `python vordur.py --source "New Tool" --dry-run`
5. Done. Next cron run picks it up.
