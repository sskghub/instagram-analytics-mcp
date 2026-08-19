# Instagram Analytics MCP

An MCP server that answers questions about Instagram reel performance in plain language,
across multiple accounts.

The point is not that it wraps an API. It is that **the number that actually predicts reach
does not exist in the Instagram API**, so the server computes it.

```
"How did my last 10 reels do?"
"What worked best this month?"
"Which of my accounts is working?"
```

## The problem this solves

Instagram's Graph API returns views, reach, saves, shares and average watch time.

It does not return **completion rate** -- the share of the video people actually watch. On a
real account measured over ~700 reels, completion is what separates a reel that dies from one
that travels:

| Completion | Typical outcome |
|---|---|
| under 15% | dies, a few hundred views |
| 25%+ | reliably reaches thousands |
| ~39% | went viral (161K) |

Views are the outcome. Completion is the cause, and it is readable within hours of posting
rather than days.

Computing it needs `avg_watch_time / duration`. **Duration is not in the API either.** So the
server probes each video's `media_url` with `ffprobe` to measure it.

That is the whole reason this exists. Two hops the API will not do for you, plus a threshold
judgement the API has no opinion about.

## Tools

| Tool | Answers |
|---|---|
| `recent_reels(account, limit)` | "How did my recent posts do?" |
| `top_reels(account, days, scan)` | "What actually worked?" -- ranked by completion, not views |
| `compare_accounts(days, scan)` | "Which account is working?" -- median completion per account |

Every reel comes back with date, completion, a `verdict` label, duration, views, reach, saves,
shares, the caption's first line as the hook, and a permalink.

## Setup

Requires Python 3.10+ and `ffprobe` (`brew install ffmpeg`).

```bash
git clone https://github.com/sskghub/instagram-analytics-mcp
cd instagram-analytics-mcp

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env    # then fill in your tokens
```

Verify before wiring it to anything:

```bash
.venv/bin/python server.py --selftest
.venv/bin/python test_server.py
```

Register with Claude Code:

```bash
claude mcp add ig-analytics -- /absolute/path/.venv/bin/python /absolute/path/server.py
```

The server reads its own `.env`, so **no credentials go in the MCP config file**. That config
is committed; tokens are not.

Adding an account is one row in `ACCOUNTS` in [server.py](server.py) plus two keys in `.env`.

## Token expiry

Instagram tokens last ~60 days. When one dies, everything downstream silently returns nothing.

`refresh_tokens.py` exchanges a still-valid token for a fresh 60-day one:

```bash
python refresh_tokens.py --if-older-than 7
```

Run it weekly. The constraint that shapes the design: **an expired token cannot be refreshed.**
Meta will not renew a dead token, so refreshing early is the only strategy that works. Each
refresh resets the full 60 days, so early refreshes cost nothing.

It backs up `.env` before writing, rewrites duplicate keys, and alerts on failure.

Refreshing does not invalidate the old token, so several machines can each refresh their own
`.env` independently. Token values never need syncing between hosts.

## Notes from building it

Things that cost real time, kept here because they are the parts that generalize.

**`sys.exit()` is fine in a CLI and fatal in a server.** The first version reused a function
from an existing command-line script. That function called `sys.exit()` when a token was
rejected, which would have killed the whole server process on the day a token expired. Tools
now raise `ValueError`; the SDK turns standard exceptions into readable results the model can
act on, and the server survives.

**Errors should say what to do.** A dead token returns the steps to regenerate it, not a stack
trace. The model can relay that to a human who can actually fix it.

**The docstring is the interface.** It is how the model decides whether to call a tool at all,
so each one says *when* to reach for it, not just what it returns.

**Scheduled jobs can fail silently.** On macOS a launchd timer for the refresh script failed
with `Operation not permitted`, because TCC blocks background agents from reading protected
directories. It reported as loaded and would have quietly never run. Forcing a run and reading
the log is the only way that surfaces.

**Duplicate keys in `.env` are a real trap.** A stale duplicate can shadow a freshly written
token depending on how the loader resolves them, so the writer rewrites every occurrence
rather than the first.

**The API changed names.** It is `mcp.server.mcpserver.MCPServer`; the older
`mcp.server.fastmcp.FastMCP` path was removed in mcp 2.x, along with other legacy modules.
Most examples online still show the old import and will not run.

## License

MIT
