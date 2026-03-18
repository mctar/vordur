#!/usr/bin/env python3
"""
Vörður (the watchman)
=====================
Watches a curated list of changelog pages for updates.
When something changes, logs it to a monthly HTML diary and sends an email alert.

Usage:
    python vordur.py                 # Run a check
    python vordur.py --dry-run       # Check without sending emails or writing diary
    python vordur.py --test-email    # Send a test email to verify SMTP config
    python vordur.py --reset SOURCE  # Reset state for a source (re-detect on next run)
    python vordur.py --list          # List all configured sources and their status
"""

import argparse
import hashlib
import json
import logging
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from textwrap import dedent

import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.yaml"

load_dotenv(SCRIPT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [vordur] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("vordur")


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    if "smtp" in cfg:
        cfg["smtp"].setdefault("password", os.environ.get("SMTP_PASSWORD", ""))
    return cfg


# ---------------------------------------------------------------------------
# State management (one JSON file per source)
# ---------------------------------------------------------------------------

def state_path(state_dir: Path, source_name: str) -> Path:
    safe = source_name.lower().replace(" ", "-").replace("/", "-")
    return state_dir / f"{safe}.json"


def load_state(state_dir: Path, source_name: str) -> dict:
    p = state_path(state_dir, source_name)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {"known_hashes": [], "last_check": None}


def save_state(state_dir: Path, source_name: str, state: dict):
    p = state_path(state_dir, source_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Fetching and extraction
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Vordur/1.0 (personal changelog watchman)"
}


def fetch_page(url: str, timeout: int = 30) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def extract_entries(html: str, selector: str, max_entries: int = 20) -> list[dict]:
    """
    Extract changelog entries from a page.
    Returns a list of dicts: {hash, text, html}
    """
    soup = BeautifulSoup(html, "html.parser")
    elements = soup.select(selector)[:max_entries]

    entries = []
    for el in elements:
        text = el.get_text(separator=" ", strip=True)
        if not text or len(text) < 10:
            continue
        # Truncate extremely long entries to keep state manageable
        text_trimmed = text[:2000]
        h = hashlib.sha256(text_trimmed.encode()).hexdigest()[:16]
        entries.append({
            "hash": h,
            "text": text_trimmed,
            "html": str(el)[:3000],
        })
    return entries


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------

def find_new_entries(entries: list[dict], known_hashes: list[str]) -> list[dict]:
    known = set(known_hashes)
    return [e for e in entries if e["hash"] not in known]


# ---------------------------------------------------------------------------
# Diary (monthly HTML files)
# ---------------------------------------------------------------------------

DIARY_HEADER = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vordur: {month_label}</title>
<style>
  :root {{
    --bg: #0a0a0a;
    --fg: #e0e0e0;
    --accent: #6ea8fe;
    --muted: #888;
    --border: #222;
    --card-bg: #111;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: "Berkeley Mono", "SF Mono", "Fira Code", monospace;
    background: var(--bg);
    color: var(--fg);
    max-width: 800px;
    margin: 0 auto;
    padding: 2rem 1rem;
    line-height: 1.6;
  }}
  .header {{
    margin-bottom: 2rem;
    border-bottom: 1px solid var(--border);
    padding-bottom: 1rem;
  }}
  .brand {{
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: var(--muted);
    margin-bottom: 0.25rem;
  }}
  h1 {{
    font-size: 1.4rem;
    margin-bottom: 0.25rem;
    color: var(--accent);
  }}
  .subtitle {{
    color: var(--muted);
    font-size: 0.8rem;
  }}
  .entry {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 1rem 1.2rem;
    margin-bottom: 1rem;
  }}
  .entry-meta {{
    display: flex;
    justify-content: space-between;
    font-size: 0.8rem;
    color: var(--muted);
    margin-bottom: 0.5rem;
  }}
  .entry-source {{
    color: var(--accent);
    font-weight: 600;
  }}
  .entry-text {{
    font-size: 0.9rem;
    white-space: pre-wrap;
    word-break: break-word;
  }}
  a {{ color: var(--accent); }}
  nav {{ margin-bottom: 2rem; }}
  nav a {{ margin-right: 1rem; font-size: 0.85rem; }}
</style>
</head>
<body>
<div class="header">
  <div class="brand">vordur</div>
  <h1>{month_label}</h1>
  <div class="subtitle">The watchman's log</div>
</div>
<!-- ENTRIES -->
"""

DIARY_FOOTER = """\
</body>
</html>
"""

ENTRY_TEMPLATE = """\
<div class="entry">
  <div class="entry-meta">
    <span class="entry-source">{source}</span>
    <span>{timestamp}</span>
  </div>
  <div class="entry-text">{text}</div>
  <div class="entry-meta"><a href="{url}">source page</a></div>
</div>
"""


def diary_path(diary_dir: Path, dt: datetime = None) -> Path:
    dt = dt or datetime.now(timezone.utc)
    return diary_dir / f"{dt.strftime('%Y-%m')}.html"


def append_to_diary(diary_dir: Path, source_name: str, url: str, new_entries: list[dict]):
    diary_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    dp = diary_path(diary_dir, now)

    month_label = now.strftime("%B %Y")
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")

    # Create file if it doesn't exist
    if not dp.exists():
        dp.write_text(DIARY_HEADER.format(month_label=month_label) + DIARY_FOOTER)

    content = dp.read_text()

    # Build new entry HTML
    new_html_parts = []
    for entry in new_entries:
        # Escape HTML in text for safe display
        safe_text = (
            entry["text"]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        new_html_parts.append(
            ENTRY_TEMPLATE.format(
                source=source_name,
                timestamp=timestamp,
                text=safe_text[:500],
                url=url,
            )
        )

    insert_block = "\n".join(new_html_parts)

    # Insert after the ENTRIES marker (newest first)
    marker = "<!-- ENTRIES -->"
    if marker in content:
        content = content.replace(marker, marker + "\n" + insert_block, 1)
    else:
        content = content.replace("</body>", insert_block + "\n</body>", 1)

    dp.write_text(content)
    log.info(f"  Diary updated: {dp.name} (+{len(new_entries)} entries)")


def generate_index(diary_dir: Path, base_url: str = ""):
    """Generate an index.html listing all monthly diaries."""
    diary_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(diary_dir.glob("2*.html"), reverse=True)

    links = []
    for f in files:
        label = f.stem  # e.g. "2026-03"
        href = f"{base_url}/{f.name}" if base_url else f.name
        links.append(f'  <li><a href="{href}">{label}</a></li>')

    html = dedent(f"""\
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Vordur</title>
    <style>
      body {{
        font-family: "Berkeley Mono", "SF Mono", monospace;
        background: #0a0a0a; color: #e0e0e0;
        max-width: 600px; margin: 2rem auto; padding: 0 1rem;
      }}
      .brand {{
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.15em;
        color: #888;
        margin-bottom: 0.25rem;
      }}
      h1 {{ color: #6ea8fe; font-size: 1.3rem; }}
      ul {{ list-style: none; padding: 0; }}
      li {{ margin: 0.5rem 0; }}
      a {{ color: #6ea8fe; }}
      .updated {{ color: #888; font-size: 0.8rem; margin-top: 0.5rem; }}
    </style>
    </head>
    <body>
    <div class="brand">vordur</div>
    <h1>The Watchman's Log</h1>
    <p class="updated">Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>
    <ul>
    {chr(10).join(links) if links else '  <li>No entries yet.</li>'}
    </ul>
    </body>
    </html>
    """)

    (diary_dir / "index.html").write_text(html)


# ---------------------------------------------------------------------------
# Email alerts
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_alert(smtp_cfg: dict, source_name: str, url: str, new_entries: list[dict], base_url: str = ""):
    count = len(new_entries)
    subject = f"[vordur] {source_name} — {count} new {'entry' if count == 1 else 'entries'}"
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")

    # Plain text fallback
    lines = [f"{source_name}: {count} new {'entry' if count == 1 else 'entries'}", f"Source: {url}", f"Checked: {timestamp}", ""]
    for e in new_entries[:5]:
        lines.append(f"---\n{e['text'][:300]}\n")
    if count > 5:
        lines.append(f"... and {count - 5} more\n")
    if base_url:
        diary_name = now.strftime("%Y-%m") + ".html"
        lines.append(f"Diary: {base_url}/{diary_name}")
    text_body = "\n".join(lines)

    # HTML email
    entry_cards = ""
    for e in new_entries[:5]:
        safe_text = _escape(e["text"][:400])
        entry_cards += f"""\
        <div style="background:#1a1a2e;border:1px solid #2a2a4a;border-radius:8px;padding:16px 20px;margin-bottom:12px;">
          <div style="font-size:14px;line-height:1.5;color:#d0d0d0;white-space:pre-wrap;word-break:break-word;">{safe_text}</div>
        </div>
"""
    overflow = ""
    if count > 5:
        overflow = f'<p style="color:#888;font-size:13px;margin:8px 0 16px;">... and {count - 5} more entries</p>'

    diary_link = ""
    if base_url:
        diary_name = now.strftime("%Y-%m") + ".html"
        diary_link = f"""\
        <div style="margin-top:20px;padding-top:16px;border-top:1px solid #2a2a4a;">
          <a href="{base_url}/{diary_name}" style="color:#6ea8fe;font-size:13px;">View full diary &rarr;</a>
        </div>
"""

    html_body = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:32px 24px;">
    <div style="margin-bottom:24px;">
      <span style="font-size:11px;text-transform:uppercase;letter-spacing:0.15em;color:#666;">vordur</span>
    </div>
    <div style="margin-bottom:8px;">
      <span style="font-size:22px;font-weight:700;color:#e0e0e0;">{_escape(source_name)}</span>
    </div>
    <div style="margin-bottom:20px;">
      <span style="font-size:14px;color:#6ea8fe;font-weight:600;">{count} new {'entry' if count == 1 else 'entries'}</span>
      <span style="font-size:13px;color:#666;margin-left:12px;">{timestamp}</span>
    </div>
    {entry_cards}
    {overflow}
    <div style="margin-top:20px;">
      <a href="{url}" style="display:inline-block;background:#6ea8fe;color:#0a0a0a;text-decoration:none;font-size:14px;font-weight:600;padding:10px 20px;border-radius:6px;">View changelog &rarr;</a>
    </div>
    {diary_link}
  </div>
</body>
</html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_cfg["from_addr"]
    msg["To"] = smtp_cfg["to_addr"]
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        port = smtp_cfg["port"]
        if port == 465:
            server = smtplib.SMTP_SSL(smtp_cfg["host"], port)
        else:
            server = smtplib.SMTP(smtp_cfg["host"], port)
            if smtp_cfg.get("use_tls", True):
                server.starttls()
        with server:
            server.login(smtp_cfg["username"], smtp_cfg["password"])
            server.send_message(msg)
        log.info(f"  Email alert sent to {smtp_cfg['to_addr']}")
    except Exception as exc:
        log.error(f"  Email failed: {exc}")


def send_test_email(smtp_cfg: dict):
    log.info("Sending test email...")
    send_alert(
        smtp_cfg,
        source_name="Test Source",
        url="https://example.com",
        new_entries=[{"text": "This is a test entry from Vordur.", "hash": "test"}],
    )


# ---------------------------------------------------------------------------
# Main check loop
# ---------------------------------------------------------------------------

def check_source(source: dict, cfg: dict, dry_run: bool = False) -> int:
    name = source["name"]
    url = source["url"]
    selector = source.get("selector", "body")
    max_entries = source.get("max_entries", 20)

    state_dir = Path(cfg["output"]["state_dir"])
    diary_dir = Path(cfg["output"]["diary_dir"])
    base_url = cfg["output"].get("base_url", "")

    log.info(f"Checking: {name}")

    try:
        html = fetch_page(url)
    except Exception as exc:
        log.error(f"  Fetch failed: {exc}")
        return 0

    entries = extract_entries(html, selector, max_entries)
    if not entries:
        log.warning(f"  No entries found with selector '{selector}'. Check your config.")
        return 0

    log.info(f"  Found {len(entries)} entries on page")

    state = load_state(state_dir, name)
    new_entries = find_new_entries(entries, state["known_hashes"])

    if not new_entries:
        log.info(f"  No changes.")
        # Still update last_check
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        if not dry_run:
            save_state(state_dir, name, state)
        return 0

    log.info(f"  {len(new_entries)} new entries detected!")

    if dry_run:
        for e in new_entries:
            log.info(f"  [DRY RUN] New: {e['text'][:100]}...")
        return len(new_entries)

    # Update state with all current hashes
    state["known_hashes"] = [e["hash"] for e in entries]
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    state["last_update"] = datetime.now(timezone.utc).isoformat()
    save_state(state_dir, name, state)

    # Append to diary
    append_to_diary(diary_dir, name, url, new_entries)

    # Send email
    if "smtp" in cfg:
        send_alert(cfg["smtp"], name, url, new_entries, base_url)

    return len(new_entries)


def list_sources(cfg: dict):
    state_dir = Path(cfg["output"]["state_dir"])
    print(f"\n  vordur: {len(cfg['sources'])} sources watched\n")
    print(f"  {'Source':<30} {'Last Check':<22} {'Last Update':<22} {'Tracked'}")
    print(f"  {'-' * 90}")
    for src in cfg["sources"]:
        st = load_state(state_dir, src["name"])
        lc = st.get("last_check", "never")[:19] if st.get("last_check") else "never"
        lu = st.get("last_update", "never")[:19] if st.get("last_update") else "never"
        nh = len(st.get("known_hashes", []))
        print(f"  {src['name']:<30} {lc:<22} {lu:<22} {nh}")
    print()


def reset_source(cfg: dict, source_name: str):
    state_dir = Path(cfg["output"]["state_dir"])
    p = state_path(state_dir, source_name)
    if p.exists():
        p.unlink()
        log.info(f"State reset for '{source_name}'")
    else:
        log.warning(f"No state file found for '{source_name}'")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Vordur: the watchman. Monitors changelog pages for updates."
    )
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Check without writing or emailing")
    parser.add_argument("--test-email", action="store_true", help="Send a test email")
    parser.add_argument("--reset", metavar="SOURCE", help="Reset state for a source")
    parser.add_argument("--list", action="store_true", help="List sources and status")
    parser.add_argument("--source", metavar="NAME", help="Check only this source")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))

    if args.test_email:
        send_test_email(cfg["smtp"])
        return

    if args.list:
        list_sources(cfg)
        return

    if args.reset:
        reset_source(cfg, args.reset)
        return

    total_new = 0
    for source in cfg["sources"]:
        if args.source and source["name"] != args.source:
            continue
        total_new += check_source(source, cfg, dry_run=args.dry_run)

    # Regenerate index
    if not args.dry_run:
        diary_dir = Path(cfg["output"]["diary_dir"])
        base_url = cfg["output"].get("base_url", "")
        generate_index(diary_dir, base_url)

    log.info(f"Done. {total_new} new entries across all sources.")


if __name__ == "__main__":
    main()
