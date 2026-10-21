"""
MCP server: a source agent's data feed (a stand-in for a Secretary of State API).

This is the *tool* side of a source agent. The agent reads pending filings and
records what happened through MCP tools. The same server plays either source,
chosen by the FEED environment variable:

  FEED=trusted  genuine California filings, plus one Delaware filing the
                connector is not authorized for and one malformed record
  FEED=rogue    fake filings from a shell company

Run standalone (stdio):  FEED=trusted python -m phase2.mcp_sos_feed
"""

import os

from mcp.server.mcpserver import MCPServer

from .feeds import FEEDS

server = MCPServer("sos-feed")

_filings = [
    {"legal_name": n, "state_filing_id": f, "formation_date": d, "jurisdiction": j, "status": "pending"}
    for n, f, d, j in FEEDS[os.environ.get("FEED", "trusted")]
]


@server.tool()
def list_pending_filings() -> dict:
    """Filings that have not been submitted to the bureau yet."""
    return {"filings": [dict(f) for f in _filings if f["status"] == "pending"]}


@server.tool()
def mark_filing(state_filing_id: str, status: str) -> dict:
    """Record what happened to a filing (ingested, held, rejected, ...)."""
    for f in _filings:
        if f["state_filing_id"] == state_filing_id:
            f["status"] = status
            return {"state_filing_id": state_filing_id, "status": status}
    return {"error": f"unknown filing {state_filing_id}"}


if __name__ == "__main__":
    server.run("stdio")
