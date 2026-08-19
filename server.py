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

# Account registry. Add an account by adding a row here plus two keys in .env.
# key -> (token env var, account id env var, display label)
ACCOUNTS = {
    "telugu": ("IG_TELUGU_TOKEN", "IG_TELUGU_ACCOUNT_ID", "Telugu"),
    "english": ("IG_ENGLISH_TOKEN", "IG_ENGLISH_ACCOUNT_ID", "English / AI"),
    "entech": ("IG_ENTECH_TOKEN", "IG_ENTECH_ACCOUNT_ID", "English / general tech"),
}

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
    if account not in ACCOUNTS:
        raise ValueError(f"unknown account {account!r}; use one of {list(ACCOUNTS)}")
    tok_env, id_env, label = ACCOUNTS[account]
    token, acc_id = os.getenv(tok_env), os.getenv(id_env)
    if not token or not acc_id:
        raise ValueError(f"{tok_env} / {id_env} missing from .env")
    return token, acc_id, label


def fetch_reels(account, limit):
    """One /media call, filtered to reels. Raises a readable error on a dead token."""
    token, acc_id, label = creds(account)
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
            f"Instagram rejected the {label} token: {r['error'].get('message')}. "
            f"Regenerate it in the Meta dashboard under Instagram -> API setup with "
            f"Instagram login -> Generate access tokens, then update "
            f"{ACCOUNTS[account][0]} in .env. An expired token cannot be refreshed."
        )
    reels = [m for m in r.get("data", []) if m.get("media_product_type") == "REELS"]
    return reels[:limit], token, label


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
    reels, token, label = fetch_reels(account, limit)
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
    return rows, label


def within(rows, days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return [r for r in rows if r["date"] >= cutoff]


mcp = MCPServer("ig-analytics")

NOTE = ("completion_pct = avg watch time / video duration, the metric that predicts reach. "
        "verdict bands are audience-specific; see BANDS in server.py.")


@mcp.tool()
def recent_reels(account: str = "telugu", limit: int = 10) -> dict:
    """How recent reels performed. Use when asked how the last few posts did, whether
    something is working, or how a specific recent reel landed.

    Returns newest first with completion rate, views, reach, saves, shares and the hook.
    Report completion before views: views are the outcome, completion is the cause and is
    readable within hours of posting.

    account is one of the keys in ACCOUNTS. limit is capped at 50.
    """
    rows, label = collect(account, limit)
    return {"account": label, "count": len(rows), "reels": rows, "note": NOTE}


@mcp.tool()
def top_reels(account: str = "telugu", days: int = 30, scan: int = 30) -> dict:
    """What actually worked, ranked by completion rate rather than views. Use when asked
    which hooks or topics performed best, what to make more of, or what to repeat.

    Ranks by completion because views are inflated by follower reach and audience size;
    completion is the honest hook score. Only reels posted in the last `days` are considered,
    drawn from the most recent `scan` reels (capped at 50).
    """
    rows, label = collect(account, scan)
    window = [r for r in within(rows, days) if r["completion_pct"] is not None]
    window.sort(key=lambda r: -r["completion_pct"])
    return {"account": label, "days": days, "count": len(window),
            "reels": window, "note": NOTE}


@mcp.tool()
def compare_accounts(days: int = 14, scan: int = 15) -> dict:
    """Side-by-side health of every configured account. Use when asked which account is
    working, how they compare, or where to put effort next.

    Reports median completion and median views per account over the window, since a single
    outlier reel distorts an average. Different accounts serve different audiences, so
    compare each against its own history before comparing them to each other.
    """
    out = {}
    for key in ACCOUNTS:
        try:
            rows, label = collect(key, scan)
        except ValueError as e:
            out[key] = {"error": str(e)}
            continue
        window = within(rows, days)
        comps = [r["completion_pct"] for r in window if r["completion_pct"] is not None]
        views = [r["views"] for r in window if r["views"] is not None]
        best = max(window, key=lambda r: r["completion_pct"] or 0, default=None)
        out[key] = {
            "account": label,
            "reels_posted": len(window),
            "median_completion_pct": round(median(comps), 1) if comps else None,
            "median_views": int(median(views)) if views else None,
            "best_reel": {"completion_pct": best["completion_pct"], "hook": best["hook"]}
            if best and best["completion_pct"] is not None else None,
        }
    return {"days": days, "accounts": out, "note": NOTE}


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        rows, label = collect("telugu", 3)
        print(f"{label}: {len(rows)} reels")
        for r in rows:
            print(f"  {r['date']}  {r['completion_pct']}%  {r['verdict']}  {r['views']} views")
        sys.exit(0)
    mcp.run()
