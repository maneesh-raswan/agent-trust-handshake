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

server = MCPServer("sos-feed")

FEEDS = {
    "trusted": [
        ("Acme Logistics LLC", "C4829301", "2024-03-15", "CA"),
        ("Birch Freight Inc", "C4829302", "2024-04-02", "CA"),
        ("Cedar Holdings LLC", "D1000234", "2024-04-09", "DE"),   # outside this connector's jurisdiction
        ("Dune Supply Co", "C4829303", "2024-13-45", "CA"),       # malformed formation date
        ("Elm Partners LP", "C4829304", "2024-05-21", "CA"),
        ("Fjord Imports Corp", "C4829305", "2024-06-30", "CA"),
        ("Granite Works LLC", "C4829306", "2024-07-11", "CA"),
        ("Harbor Cold Storage Inc", "C4829307", "2024-08-19", "CA"),
        ("Iris Medical Supply LLC", "C4829308", "2024-09-03", "CA"),
        ("Juniper Rail Services Inc", "C4829309", "2024-09-27", "CA"),
    ],
    "rogue": [
        ("Shell Corp Inc", "C9999991", "2024-01-01", "CA"),
        ("Phantom Trading LLC", "C9999992", "2024-01-02", "CA"),
        ("Mirage Capital Inc", "C9999993", "2024-01-03", "CA"),
        ("Ghost Freight LLC", "C9999994", "2024-01-04", "CA"),
        ("Vapor Holdings Corp", "C9999995", "2024-01-05", "CA"),
        ("Echo Ventures LLC", "C9999996", "2024-01-06", "CA"),
    ],
}

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
