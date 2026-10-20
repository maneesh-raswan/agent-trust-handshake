"""
MCP server: the consumer's ingestion ledger.

The consumer agent decides *whether* to ingest; this tool server owns *where*
accepted records are stored. Swapping JSONL for a database changes this file
only.

Run standalone (stdio):  LEDGER_PATH=ledger.jsonl python -m phase2.mcp_ledger
"""

import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

server = MCPServer("consumer-ledger")

LEDGER = Path(os.environ.get("LEDGER_PATH", "ledger.jsonl"))


@server.tool()
def ledger_append(entry: dict) -> dict:
    """Append one accepted record to the ledger."""
    with LEDGER.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return {"ok": True}


@server.tool()
def ledger_list() -> dict:
    """All accepted records, oldest first."""
    if not LEDGER.exists():
        return {"entries": []}
    return {"entries": [json.loads(line) for line in LEDGER.read_text().splitlines() if line]}


if __name__ == "__main__":
    server.run("stdio")
