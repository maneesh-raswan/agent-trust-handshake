"""
MCP server: the producer's ERP / accounts-receivable system.

This is the *tool* side of the producer agent. The agent reads open invoices and
records outcomes through MCP tools; it never touches the ERP's data directly.
That is the split this project demonstrates: MCP from an agent to its tools,
A2A between agents.

Run standalone (stdio):  python -m phase2.mcp_erp
"""

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("producer-erp")

COUNT = int(os.environ.get("ERP_COUNT", "10"))


def _seed() -> list[dict]:
    invoices = []
    for i in range(1, COUNT + 1):
        invoices.append({
            "txn_id": f"INV-{i:03d}",
            # One record is a credit inquiry: a type this producer was never
            # authorized to send, so the consumer will (correctly) refuse it.
            "type": "credit_inquiry" if i == 4 else "trade_payment",
            "debtor_business_id": "BIZ-778812",
            "amount": round(1000.0 + i * 1375.25, 2),
            "currency": "USD",
            "terms": "NET30",
            "days_beyond_terms": 0,
            "status": "open",
        })
    return invoices


_invoices = _seed()


@server.tool()
def list_open_invoices() -> dict:
    """Invoices that have not been sent or settled yet."""
    return {"invoices": [dict(i) for i in _invoices if i["status"] == "open"]}


@server.tool()
def mark_invoice(txn_id: str, status: str) -> dict:
    """Record the outcome of sending an invoice (settled, held, rejected, ...)."""
    for invoice in _invoices:
        if invoice["txn_id"] == txn_id:
            invoice["status"] = status
            return {"txn_id": txn_id, "status": status}
    return {"error": f"unknown invoice {txn_id}"}


@server.tool()
def list_all_invoices() -> dict:
    """Every invoice with its current status."""
    return {"invoices": [dict(i) for i in _invoices]}


if __name__ == "__main__":
    server.run("stdio")
