"""
Consumer Agent as an A2A service.

The five trust checks are the phase 1 `ConsumerAgent`, unchanged. This service
adds what a networked, autonomous consumer needs on top:

  - live status: asks the Registry agent over A2A on every transaction
  - fail closed: if the Registry cannot be reached, nothing is ingested
  - its own policy: repeated forged credentials from one key get that key
    quarantined, with no operator involved
  - recoverable errors: an expired credential yields INPUT_REQUIRED (a hint to
    renew and resend) instead of a hard rejection
  - storage through MCP: accepted records go to the ledger tool server
  - a card that publishes its receipt-signing key, so producers can verify receipts

Skills: submit_transaction, get_ledger, get_stats

Run:  python -m phase2.consumer_service --port 9102 --registry-url http://127.0.0.1:9101 \
          --registry-key-file registry.pub --ledger ledger.jsonl
"""

import argparse
import asyncio
from collections import Counter
from pathlib import Path

from google.protobuf.struct_pb2 import Struct

from trust.consumer import ConsumerAgent

from . import mcp_util
from .a2a_util import (COMPLETED, FAILED, INPUT_REQUIRED, REJECTED, Outcome, build_app, call,
                       log_event, make_card, serve)

RECEIPT_KEY_EXTENSION = "urn:agent-trust-handshake:receipt-key"
QUARANTINE_AFTER = 3

# Only forged credentials are attributable to the key presenting them. A bad
# signature or a replayed nonce could be someone else tampering with or copying a
# victim's traffic, and counting those would let an attacker get a legitimate
# producer quarantined.
STRIKE_CHECKS = {"issuer", "format"}


def peek(envelope) -> tuple[str | None, str, str]:
    """Best-effort (sender key, business id, txn id) for routing and logs. Never raises."""
    try:
        claims = envelope["credential"]["claims"]
        return (claims.get("agent_public_key"), claims.get("business_id", "?"),
                envelope["body"]["payload"].get("txn_id", "?"))
    except (KeyError, TypeError, AttributeError):
        return None, "?", "?"


class ConsumerService:
    def __init__(self, registry_url: str, registry_public_key: str, ledger_tools, log_path: str | None):
        self.registry_url = registry_url
        self.ledger = ledger_tools
        self.log = lambda kind, **d: log_event(log_path, "consumer", kind, **d)
        self._status: dict[str, str] = {}
        self._pending: list[dict] = []
        self.core = ConsumerAgent(
            registry_public_key, lambda bid: self._status.get(bid, "unknown"),
            ledger_sink=self._pending.append)
        self.strikes: Counter = Counter()
        self.quarantined: set[str] = set()
        self.stats: Counter = Counter()

    @property
    def receipt_key(self) -> str:
        return self.core.public_key

    async def submit_transaction(self, args: dict) -> Outcome:
        envelope = args.get("envelope")
        key, who, txn_id = peek(envelope)

        if key in self.quarantined:
            self.stats["quarantined_blocked"] += 1
            self.log("rejected", business_id=who, txn_id=txn_id, check="quarantine",
                     reason="sender key is quarantined")
            return Outcome(REJECTED, {"check": "quarantine", "reason": "sender key is quarantined"},
                           "sender key is quarantined after repeated forged credentials")

        # Live status from the Registry agent. Fail closed if we cannot get it.
        try:
            bid = envelope["credential"]["claims"]["business_id"]
            if not isinstance(bid, str):
                raise TypeError
            lookup = await call(self.registry_url, "get_status", {"business_id": bid})
            self._status[bid] = lookup.data["status"]
        except (KeyError, TypeError):
            pass  # malformed: the core will reject it at the format check
        except Exception as exc:
            self.stats["failed_closed"] += 1
            self.log("failed_closed", business_id=who, txn_id=txn_id, reason=f"registry unreachable: {exc}")
            return Outcome(FAILED, {"reason": "registry unreachable"}, "registry unreachable: failing closed")

        self._pending.clear()
        decision = self.core.receive(envelope if isinstance(envelope, dict) else {})

        if decision.accepted:
            await self.ledger.call("ledger_append", entry=self._pending[0])
            self.stats["accepted"] += 1
            self.log("accepted", business_id=who, txn_id=txn_id)
            return Outcome(COMPLETED, {"receipt": decision.receipt}, "ingested and settled")

        failed = next(c for c in decision.checks if not c[1])
        check, reason = failed[0], failed[2]
        checks = [list(c) for c in decision.checks]
        self.stats[f"rejected_{check}"] += 1

        if check == "expiry":
            self.log("input_required", business_id=who, txn_id=txn_id, check=check, reason=reason)
            return Outcome(INPUT_REQUIRED, {"check": check, "reason": reason, "checks": checks},
                           "credential expired: renew with the registry and resend")

        if check in STRIKE_CHECKS and key:
            self.strikes[key] += 1
            if self.strikes[key] >= QUARANTINE_AFTER:
                self.quarantined.add(key)
                self.log("quarantine", key_prefix=key[:10], strikes=self.strikes[key])
        self.log("rejected", business_id=who, txn_id=txn_id, check=check, reason=reason)
        return Outcome(REJECTED, {"check": check, "reason": reason, "checks": checks}, reason)

    async def get_ledger(self, args: dict) -> Outcome:
        return Outcome(COMPLETED, await self.ledger.call("ledger_list"))

    async def get_stats(self, args: dict) -> Outcome:
        return Outcome(COMPLETED, {"stats": dict(self.stats), "quarantined": len(self.quarantined)})


def build_consumer_app(port: int, service: ConsumerService):
    receipt_params = Struct()
    receipt_params.update({"public_key": service.receipt_key, "algorithm": "ed25519"})
    card = make_card(
        "Consumer Agent",
        "Ingestion platform. Verifies the sender's business identity before ingesting, "
        "then returns a signed settlement receipt.",
        port,
        [("submit_transaction", "Submit a signed envelope (credential + transaction + signature)"),
         ("get_ledger", "Read accepted records"),
         ("get_stats", "Decision counters")],
        extensions=[(RECEIPT_KEY_EXTENSION, "Public key that signs settlement receipts", receipt_params)],
    )
    return build_app(card, {
        "submit_transaction": service.submit_transaction,
        "get_ledger": service.get_ledger,
        "get_stats": service.get_stats,
    })


async def run(port: int, registry_url: str, registry_key_file: str, ledger: str,
              log_path: str | None) -> None:
    registry_key = Path(registry_key_file).read_text().strip()  # pinned out of band
    async with mcp_util.launch("phase2.mcp_ledger", {"LEDGER_PATH": ledger}) as tools:
        service = ConsumerService(registry_url, registry_key, tools, log_path)
        await serve(build_consumer_app(port, service), port)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9102)
    ap.add_argument("--registry-url", required=True)
    ap.add_argument("--registry-key-file", required=True)
    ap.add_argument("--ledger", default="ledger.jsonl")
    ap.add_argument("--log")
    a = ap.parse_args()
    asyncio.run(run(a.port, a.registry_url, a.registry_key_file, a.ledger, a.log))


if __name__ == "__main__":
    main()
