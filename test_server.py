#!/usr/bin/env python3
"""
Drive the server with a real MCP client over stdio.

This is the test that matters. It proves the protocol handshake, tool discovery, argument
validation and error handling independently of any particular MCP host, and it catches the
failure mode that unit tests miss: a server that imports fine but never completes a session.

    python test_server.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
SERVER = str(HERE / "server.py")


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("tools discovered:", names)
            assert {"list_accounts", "recent_reels", "top_reels",
                    "compare_accounts"} <= set(names)

            res = await session.call_tool("list_accounts", {})
            assert not res.is_error, res.content[0].text
            configured = json.loads(res.content[0].text)
            print("configured accounts:", configured["accounts"])
            assert configured["accounts"], "no accounts in .env; see SETUP.md"

            # No account argument must fall back to the default account.
            res = await session.call_tool("recent_reels", {"limit": 3})
            assert not res.is_error, res.content[0].text
            data = json.loads(res.content[0].text)
            print(f"\nrecent_reels (default account) -> {data['account']}, "
                  f"{data['count']} reels")
            for r in data["reels"]:
                print(f"   {r['date']}  {r['completion_pct']}%  {r['verdict']}")

            # An unknown account must come back as a readable error, not kill the server.
            res = await session.call_tool("recent_reels", {"account": "nope"})
            assert res.is_error
            print("\nbad account -> handled:", res.content[0].text[:70])

            # The server must still be usable after that failure.
            res = await session.call_tool("recent_reels", {"limit": 1})
            assert not res.is_error
            print("server alive after error: yes")

    print("\nall checks passed")


if __name__ == "__main__":
    asyncio.run(main())
