#!/usr/bin/env python3
"""
Keep the Instagram tokens alive so the server never goes dark.

Instagram long-lived tokens last ~60 days. The refresh endpoint exchanges a still-valid
token for a fresh 60-day one, so running this on a timer means nobody ever opens the Meta
dashboard again.

The constraint that shapes everything: an EXPIRED token cannot be refreshed. Meta will not
renew a dead token and no automation can change that. So this runs weekly, not on day 59.
Refreshing early is free because each refresh resets the full 60 days. Tokens must be at
least 24h old to be refreshable.

Refreshing does not invalidate the previous token, so multiple hosts can each refresh their
own copy of .env independently. No token values ever need to be synced between machines.

Usage:
    python refresh_tokens.py                      # refresh all, write .env
    python refresh_tokens.py --dry-run            # report only, write nothing
    python refresh_tokens.py --if-older-than 7    # skip if refreshed within 7 days
    python refresh_tokens.py --env-file /path/.env
"""

import argparse
import json
import os
import re
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

SYSTEM = "IG TOKEN REFRESH"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"

# A host only needs the accounts it actually serves. A key absent from the target .env is
# skipped, not treated as a failure, so one script works across machines with different roles.
TOKEN_KEYS = ["IG_TELUGU_TOKEN", "IG_ENGLISH_TOKEN", "IG_ENTECH_TOKEN"]

ENV_FILE = Path(__file__).resolve().parent / ".env"
STAMP = ENV_FILE.parent / ".ig_token_refresh.stamp"


def send_alert(text):
    """Alert on failure. Silent success is the point; noise on success trains you to ignore it."""
    bot, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not bot or not chat:
        print("WARN: Telegram not configured; printing alert below.")
        print(text)
        return
    payload = json.dumps({"chat_id": chat, "text": text,
                          "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{bot}/sendMessage",
                                 data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"WARN: alert send failed: {e}")


def refresh(token):
    """Exchange a still-valid token for a fresh 60-day one."""
    j = requests.get(REFRESH_URL,
                     params={"grant_type": "ig_refresh_token", "access_token": token},
                     timeout=30).json()
    if "error" in j:
        raise RuntimeError(j["error"].get("message", "unknown error"))
    return j["access_token"], j.get("expires_in", 0) / 86400


def write_env(updates):
    """Replace token values in place, preserving comments, order and every other key."""
    backup = ENV_FILE.parent / f".env.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(ENV_FILE, backup)

    seen = {k: 0 for k in updates}
    out = []
    for line in ENV_FILE.read_text().splitlines(keepends=True):
        m = re.match(r"^(\s*)([A-Z0-9_]+)\s*=", line)
        key = m.group(2) if m else None
        if key in updates:
            seen[key] += 1
            # Rewrite every occurrence, not just the first. A duplicate key is a real trap:
            # loaders disagree on which one wins, so a stale duplicate can silently shadow
            # a freshly written token.
            out.append(f"{key}={updates[key]}\n")
        else:
            out.append(line)
    ENV_FILE.write_text("".join(out))
    return backup, {k: n for k, n in seen.items() if n > 1}


def main():
    global ENV_FILE, STAMP
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--if-older-than", type=float, metavar="DAYS",
                    help="exit immediately if refreshed more recently than this")
    ap.add_argument("--env-file", metavar="PATH", help="target .env")
    args = ap.parse_args()

    if args.env_file:
        ENV_FILE = Path(args.env_file).resolve()
        STAMP = ENV_FILE.parent / ".ig_token_refresh.stamp"
    if not ENV_FILE.exists():
        sys.exit(f"env file not found: {ENV_FILE}")
    load_dotenv(ENV_FILE, override=True)
    print(f"target: {ENV_FILE}")

    if args.if_older_than is not None and STAMP.exists():
        age = (datetime.now().timestamp() - STAMP.stat().st_mtime) / 86400
        if age < args.if_older_than:
            print(f"refreshed {age:.1f} days ago, skipping")
            return 0

    body = ENV_FILE.read_text()
    present = {k for k in TOKEN_KEYS if re.search(rf"^{k}=", body, re.M)}

    updates, failures = {}, []
    for key in TOKEN_KEYS:
        if key not in present:
            print(f"{key}: not on this host, skipping")
            continue
        token = os.getenv(key)
        if not token:
            failures.append(f"{key}: present but empty")
            continue
        try:
            new, days = refresh(token)
            updates[key] = new
            print(f"{key}: refreshed, valid {days:.0f} days")
        except Exception as e:
            failures.append(f"{key}: {e}")
            print(f"{key}: FAILED -- {e}")

    if failures:
        send_alert(f"{SYSTEM} -- TOKEN REFRESH FAILED\n\n" + "\n".join(failures)
                   + "\n\nAn expired token cannot be refreshed. Regenerate it in the Meta "
                     "dashboard: Instagram -> API setup with Instagram login -> "
                     "Generate access tokens.")

    if args.dry_run:
        print(f"\ndry run: would update {len(updates)} key(s) in {ENV_FILE}")
        return 1 if failures else 0

    if updates:
        backup, dupes = write_env(updates)
        STAMP.write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n")
        print(f"\nwrote {len(updates)} token(s) to {ENV_FILE}")
        print(f"backup: {backup.name}")
        if dupes:
            print(f"NOTE: duplicate keys rewritten: {dupes}")

    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        send_alert(f"{SYSTEM} -- CRASHED\n\n{type(e).__name__}: {e}")
        raise
