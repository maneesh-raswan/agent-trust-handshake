"""
Bureau Core as an A2A service: the ingestion and verification engine.

The authentication module, ingestion pipeline and audit logger are the core
`BureauCore`, unchanged from phase 1. This service adds what a networked,
autonomous bureau needs on top:

  - live registry lookup: asks the registry agent over A2A on every submission
  - fail closed: if the registry cannot be reached, nothing is ingested
  - quarantine policy: a source that keeps presenting an agent_id the registry
    has never heard of is blocked outright, with no operator involved
  - storage through MCP: verified filings and audit entries go to the store tool server

Skills: submit_filing (public); get_store, get_audit (operator-only, need TRUST_ADMIN_TOKEN)

Run:  python -m phase2.bureau_core --port 9102 --registry-url http://127.0.0.1:9101 --store-dir ./data
"""

import argparse
import asyncio
from collections import Counter

from trust.bureau import BureauCore

from . import mcp_util
from .a2a_util import (COMPLETED, FAILED, REJECTED, Outcome, build_app, call, log_event, make_card,
                       require_admin, serve)

QUARANTINE_AFTER = 3
QUARANTINED = "QUARANTINED_SOURCE_REJECTED"


class BureauService:
    def __init__(self, registry_url: str, store_tools, log_path: str | None):
        self.registry_url, self.store = registry_url, store_tools
        self.log = lambda kind, **d: log_event(log_path, "bureau", kind, **d)
        self._agents: dict[str, dict | None] = {}
        self._to_store: list[dict] = []
        self._to_audit: list[dict] = []
        self.core = BureauCore(lambda agent_id: self._agents.get(agent_id),
                               store=self._to_store.append, audit_sink=self._to_audit.append)
        self.strikes: Counter = Counter()
        self.quarantined: set[str] = set()

    async def submit_filing(self, args: dict) -> Outcome:
        envelope = args.get("envelope")
        meta = envelope.get("agent_metadata") if isinstance(envelope, dict) else None
        agent_id = meta.get("agent_id") if isinstance(meta, dict) else None
        agent_id = agent_id if isinstance(agent_id, str) else None
        filing = (envelope.get("business_filing") if isinstance(envelope, dict) else None) or {}
        label = filing.get("legal_name", "?") if isinstance(filing, dict) else "?"

        if agent_id in self.quarantined:
            return await self._reject_quarantined(envelope, agent_id, label)

        if agent_id:  # live trust status from the registry agent; fail closed if unreachable
            try:
                self._agents[agent_id] = (await call(self.registry_url, "lookup_agent",
                                                     {"agent_id": agent_id})).data["agent"]
            except Exception as exc:
                self.log("failed_closed", agent_id=agent_id, reason=f"registry unreachable: {exc}")
                return Outcome(FAILED, {"reason": "registry unreachable"}, "registry unreachable: failing closed")

        self._to_store.clear()
        self._to_audit.clear()
        decision = self.core.submit(envelope)
        for record in self._to_store:
            await self.store.call("store_filing", record=record)
        for entry in self._to_audit:
            await self.store.call("audit_append", entry=entry)

        # An unknown agent_id is attributable to whoever sent it. A known one is not: anyone
        # could claim a trusted agent's id to get it blocked, so only unknown ids earn strikes.
        if decision.check == "identity" and agent_id:
            self.strikes[agent_id] += 1
            if self.strikes[agent_id] >= QUARANTINE_AFTER:
                self.quarantined.add(agent_id)
                self.log("quarantined", agent_id=agent_id, strikes=self.strikes[agent_id])

        self.log("decided", agent_id=agent_id, legal_name=label, status=decision.status,
                 check=decision.check, reason=decision.reason)
        state = COMPLETED if decision.accepted else REJECTED
        return Outcome(state, {"status": decision.status, "check": decision.check, "reason": decision.reason},
                       decision.reason)

    async def _reject_quarantined(self, envelope, agent_id: str, label: str) -> Outcome:
        reason = "source is quarantined after repeated unauthorized submissions"
        entry = {"status": QUARANTINED, "agent_id": agent_id, "legal_name": label, "check": "quarantine",
                 "reason": reason, "state_filing_id": (envelope.get("business_filing") or {}).get("state_filing_id")}
        await self.store.call("audit_append", entry=entry)
        self.log("decided", agent_id=agent_id, legal_name=label, status=QUARANTINED,
                 check="quarantine", reason=reason)
        return Outcome(REJECTED, {"status": QUARANTINED, "check": "quarantine", "reason": reason}, reason)

    async def get_store(self, args: dict) -> Outcome:
        return require_admin(args) or Outcome(COMPLETED, await self.store.call("read_store"))

    async def get_audit(self, args: dict) -> Outcome:
        return require_admin(args) or Outcome(COMPLETED, await self.store.call("read_audit"))


def build_bureau_app(port: int, service: BureauService):
    card = make_card(
        "Bureau Core",
        "Ingestion and verification engine. Authenticates the source agent before it looks at any filing.",
        port,
        [("submit_filing", "Submit a signed business formation filing")],
        # get_store and get_audit are operator-only and deliberately not advertised
    )
    return build_app(card, {"submit_filing": service.submit_filing, "get_store": service.get_store,
                            "get_audit": service.get_audit})


async def run(port: int, registry_url: str, store_dir: str, log_path: str | None) -> None:
    async with mcp_util.launch("phase2.mcp_bureau_store", {"STORE_DIR": store_dir}) as tools:
        await serve(build_bureau_app(port, BureauService(registry_url, tools, log_path)), port)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9102)
    ap.add_argument("--registry-url", required=True)
    ap.add_argument("--store-dir", default=".")
    ap.add_argument("--log")
    a = ap.parse_args()
    asyncio.run(run(a.port, a.registry_url, a.store_dir, a.log))


if __name__ == "__main__":
    main()
