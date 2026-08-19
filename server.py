#!/usr/bin/env python3
"""
Instagram Analytics MCP -- exposes reel performance to an LLM.

The metric that predicts reach is completion rate, and the Instagram API does not return it.
It has to be computed as ig_reels_avg_watch_time / duration, and duration is not in the API
either, so every video is probed with ffprobe. That gap is the reason this server exists: it
hands the model the number that matters, not the numbers the API happens to expose.

Deliberately standalone rather than importing an existing CLI script: that script called
sys.exit() on a dead token, which is correct for a CLI and fatal for a long-running server.

Smoke test without an MCP client:
    python server.py --selftest
"""

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import requests
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

GRAPH = "https://graph.instagram.com/v21.0"
METRICS = "views,reach,saved,shares,total_interactions,ig_reels_avg_watch_time"
MAX_LIMIT = 50  # ~15s round trip; beyond this the tool call risks timing out
WORKERS = 12


def discover_accounts():
    """Accounts are defined in .env, so adding one never means editing this file.

        IG_ACCOUNT_MAIN_TOKEN + IG_ACCOUNT_MAIN_ID  ->  account key "main"

    Names are letters and digits only; an underscore would make the pattern ambiguous.
    """
    found = {}
    for key, token in os.environ.items():
        m = re.fullmatch(r"IG_ACCOUNT_([A-Z0-9]+)_TOKEN", key)
        if not m or not token.strip():
            continue
        acc_id = (os.getenv(f"IG_ACCOUNT_{m.group(1)}_ID") or "").strip()
        if acc_id:
            found[m.group(1).lower()] = (token.strip(), acc_id)
    return dict(sorted(found.items()))


ACCOUNTS = discover_accounts()

# Completion bands, measured on one real account over ~700 reels. Under 15% the reel dies;
# over 25% it reliably reaches thousands; ~39% was the viral threshold. Calibrate these
# against your own history before trusting them -- they are audience-specific, not universal.
BANDS = [(15, "dies"), (25, "weak"), (39, "strong")]


def verdict(pct):
    if pct is None:
        return None
    for threshold, label in BANDS:
        if pct < threshold:
            return label
    return "viral"


def creds(account):
    if not ACCOUNTS:
        raise ValueError(
            "No accounts configured. Add IG_ACCOUNT_<NAME>_TOKEN and IG_ACCOUNT_<NAME>_ID "
            "to .env. See SETUP.md for how to get them."
        )
    account = (account or next(iter(ACCOUNTS))).lower()
    if account not in ACCOUNTS:
        raise ValueError(f"unknown account {account!r}; configured: {list(ACCOUNTS)}")
    token, acc_id = ACCOUNTS[account]
    return token, acc_id, account


def fetch_reels(account, limit):
    """One /media call, filtered to reels. Raises a readable error on a dead token."""
    token, acc_id, name = creds(account)
    r = requests.get(
        f"{GRAPH}/{acc_id}/media",
        params={
            "fields": "id,media_product_type,caption,timestamp,permalink,media_url",
            "limit": min(limit * 3, 100),
            "access_token": token,
        },
        timeout=30,
    ).json()
    if "error" in r:
        raise ValueError(
            f"Instagram rejected the {name!r} token: {r['error'].get('message')}. "
            f"Regenerate it in the Meta dashboard under Instagram -> API setup with "
            f"Instagram login -> Generate access tokens, then update "
            f"IG_ACCOUNT_{name.upper()}_TOKEN in .env. An expired token cannot be refreshed; "
            f"see SETUP.md."
        )
    reels = [m for m in r.get("data", []) if m.get("media_product_type") == "REELS"]
    return reels[:limit], token, name


def fetch_insights(media_id, token):
    r = requests.get(
        f"{GRAPH}/{media_id}/insights",
        params={"metric": METRICS, "access_token": token},
        timeout=30,
    ).json()
    return {m["name"]: m["values"][0]["value"] for m in r.get("data", [])}


def probe_duration(url):
    """Duration is not in the API. Probe the video file itself."""
    if not url:
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", url],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        return round(float(out), 2) if out else None
    except Exception:
        return None


def collect(account, limit):
    """Reels enriched with insights and completion rate, newest first."""
    limit = max(1, min(limit, MAX_LIMIT))
    reels, token, name = fetch_reels(account, limit)
    with ThreadPoolExecutor(WORKERS) as pool:
        insights = list(pool.map(lambda m: fetch_insights(m["id"], token), reels))
    with ThreadPoolExecutor(WORKERS) as pool:
        durations = list(pool.map(lambda m: probe_duration(m.get("media_url")), reels))

    rows = []
    for m, ins, dur in zip(reels, insights, durations):
        watch_ms = ins.get("ig_reels_avg_watch_time")
        pct = round((watch_ms / 1000) / dur * 100, 1) if watch_ms and dur else None
        rows.append({
            "date": (m.get("timestamp") or "")[:10],
            "completion_pct": pct,
            "verdict": verdict(pct),
            "duration_s": dur,
            "views": ins.get("views"),
            "reach": ins.get("reach"),
            "saves": ins.get("saved"),
            "shares": ins.get("shares"),
            "hook": (m.get("caption") or "").split("\n")[0][:90],
            "permalink": m.get("permalink"),
        })
    return rows, name


def within(rows, days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return [r for r in rows if r["date"] >= cutoff]


mcp = MCPServer("ig-analytics")

NOTE = ("completion_pct = avg watch time / video duration, the metric that predicts reach. "
        "verdict bands are audience-specific; see BANDS in server.py.")


@mcp.tool()
def list_accounts() -> dict:
    """Which Instagram accounts are configured. Use first when the user has more than one
    account, or when an account name in a request is ambiguous or unrecognised."""
    return {"accounts": list(ACCOUNTS),
            "default": next(iter(ACCOUNTS)) if ACCOUNTS else None}


@mcp.tool()
def recent_reels(account: str = "", limit: int = 10) -> dict:
    """How recent reels performed. Use when asked how the last few posts did, whether
    something is working, or how a specific recent reel landed.

    Returns newest first with completion rate, views, reach, saves, shares and the hook.
    Report completion before views: views are the outcome, completion is the cause and is
    readable within hours of posting.

    account defaults to the first configured account; call list_accounts to see the names.
    limit is capped at 50.
    """
    rows, name = collect(account, limit)
    return {"account": name, "count": len(rows), "reels": rows, "note": NOTE}


@mcp.tool()
def top_reels(account: str = "", days: int = 30, scan: int = 30) -> dict:
    """What actually worked, ranked by completion rate rather than views. Use when asked
    which hooks or topics performed best, what to make more of, or what to repeat.

    Ranks by completion because views are inflated by follower reach and audience size;
    completion is the honest hook score. Only reels posted in the last `days` are considered,
    drawn from the most recent `scan` reels (capped at 50).
    """
    rows, name = collect(account, scan)
    window = [r for r in within(rows, days) if r["completion_pct"] is not None]
    window.sort(key=lambda r: -r["completion_pct"])
    return {"account": name, "days": days, "count": len(window),
            "reels": window, "note": NOTE}


@mcp.tool()
def compare_accounts(days: int = 14, scan: int = 15) -> dict:
    """Side-by-side health of every configured account. Use when asked which account is
    working, how they compare, or where to put effort next. Only useful with 2+ accounts.

    Reports median completion and median views per account over the window, since a single
    outlier reel distorts an average. Different accounts serve different audiences, so
    compare each against its own history before comparing them to each other.
    """
    out = {}
    for key in ACCOUNTS:
        try:
            rows, name = collect(key, scan)
        except ValueError as e:
            out[key] = {"error": str(e)}
            continue
        window = within(rows, days)
        comps = [r["completion_pct"] for r in window if r["completion_pct"] is not None]
        views = [r["views"] for r in window if r["views"] is not None]
        best = max(window, key=lambda r: r["completion_pct"] or 0, default=None)
        out[key] = {
            "reels_posted": len(window),
            "median_completion_pct": round(median(comps), 1) if comps else None,
            "median_views": int(median(views)) if views else None,
            "best_reel": {"completion_pct": best["completion_pct"], "hook": best["hook"]}
            if best and best["completion_pct"] is not None else None,
        }
    return {"days": days, "accounts": out, "note": NOTE}


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        if not ACCOUNTS:
            sys.exit("No accounts configured. See SETUP.md.")
        print(f"configured accounts: {list(ACCOUNTS)}")
        rows, name = collect("", 3)
        print(f"\n{name}: {len(rows)} reels")
        for r in rows:
            print(f"  {r['date']}  {r['completion_pct']}%  {r['verdict']}  {r['views']} views")
        sys.exit(0)
    mcp.run()
