#!/usr/bin/env python3
"""
Check your setup before wiring anything to an MCP client.

Run this right after pasting a token into .env. It tells you whether the token works, what
account it belongs to, and the numeric account id to paste back in -- so you never have to
hunt for the id in the Meta dashboard.

    python check_setup.py
"""

import os
import re
import shutil
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ENV = HERE / ".env"

OK, BAD, WARN = "  OK  ", " FAIL ", " WARN "


def main():
    print("Instagram Analytics MCP -- setup check\n")
    problems = 0

    # 1. Dependencies
    try:
        import mcp  # noqa: F401
        print(f"[{OK}] mcp package installed")
    except ImportError:
        print(f"[{BAD}] mcp package missing. Run: pip install -r requirements.txt")
        problems += 1

    if shutil.which("ffprobe"):
        print(f"[{OK}] ffprobe found")
    else:
        print(f"[{BAD}] ffprobe not found. Install ffmpeg (macOS: brew install ffmpeg)")
        print("         Without it there is no duration, so no completion rate.")
        problems += 1

    # 2. .env exists
    if not ENV.exists():
        print(f"[{BAD}] no .env file. Run: cp .env.example .env")
        return 1
    print(f"[{OK}] .env found")
    load_dotenv(ENV, override=True)

    # 3. Accounts
    names = sorted(set(re.findall(r"^IG_ACCOUNT_([A-Z0-9]+)_TOKEN=", ENV.read_text(), re.M)))
    if not names:
        print(f"[{BAD}] no IG_ACCOUNT_<NAME>_TOKEN keys in .env. See SETUP.md.")
        return 1

    print(f"\nfound {len(names)} account block(s): {', '.join(n.lower() for n in names)}\n")

    for name in names:
        token = (os.getenv(f"IG_ACCOUNT_{name}_TOKEN") or "").strip()
        acc_id = (os.getenv(f"IG_ACCOUNT_{name}_ID") or "").strip()

        if not token:
            print(f"[{BAD}] {name.lower()}: token is empty. Paste it into .env.")
            problems += 1
            continue

        try:
            me = requests.get("https://graph.instagram.com/v21.0/me",
                              params={"fields": "id,username", "access_token": token},
                              timeout=20).json()
        except Exception as e:
            print(f"[{BAD}] {name.lower()}: could not reach Instagram ({e})")
            problems += 1
            continue

        if "error" in me:
            msg = me["error"].get("message", "")
            print(f"[{BAD}] {name.lower()}: token rejected -- {msg}")
            print("         If it expired, generate a new one. Expired tokens cannot be refreshed.")
            problems += 1
            continue

        real_id, username = str(me.get("id", "")), me.get("username", "?")
        print(f"[{OK}] {name.lower()}: token works, account @{username}")

        if not acc_id:
            print(f"[{WARN}] {name.lower()}: IG_ACCOUNT_{name}_ID is empty. Paste this into .env:")
            print(f"\n         IG_ACCOUNT_{name}_ID={real_id}\n")
            problems += 1
        elif acc_id != real_id:
            print(f"[{WARN}] {name.lower()}: IG_ACCOUNT_{name}_ID does not match this token.")
            print(f"         .env has {acc_id}, token belongs to {real_id}")
            problems += 1

    print()
    if problems:
        print(f"{problems} thing(s) to fix. Re-run this after fixing them.")
        return 1

    print("All good. Try it:  python server.py --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
