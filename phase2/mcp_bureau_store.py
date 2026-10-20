"""
MCP server: the bureau's verified store and audit log.

The Bureau Core decides *whether* to ingest; this tool server owns *where*
verified filings and audit entries are kept. Swapping JSONL for a database
changes this file only.

Run standalone (stdio):  STORE_DIR=./data python -m phase2.mcp_bureau_store
"""

import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

server = MCPServer("bureau-store")

DIR = Path(os.environ.get("STORE_DIR", "."))
STORE, AUDIT = DIR / "verified_store.jsonl", DIR / "audit_log.jsonl"


def _append(path: Path, entry: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


@server.tool()
def store_filing(record: dict) -> dict:
    """Add one verified filing to the verified store."""
    _append(STORE, record)
    return {"ok": True}


@server.tool()
def audit_append(entry: dict) -> dict:
    """Append one audit entry (accepted or rejected)."""
    _append(AUDIT, entry)
    return {"ok": True}


@server.tool()
def read_store() -> dict:
    return {"filings": _read(STORE)}


@server.tool()
def read_audit() -> dict:
    return {"entries": _read(AUDIT)}


if __name__ == "__main__":
    server.run("stdio")
