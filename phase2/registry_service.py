"""
Trust Registry as an A2A service.

Wraps the phase 1 `TrustRegistry` (same signing, same credentials) and adds the
parts that only make sense once it is a networked agent:

  - a KYB directory: only businesses it has verified offline can onboard
  - least privilege: granted scopes = requested scopes ∩ approved scopes
  - autonomous risk policy: a high-severity watchlist event revokes a business
    immediately, with no human in the loop

Skills: register_business, renew_credential, get_status, watchlist_event

Run:  python -m phase2.registry_service --port 9101 --key-file registry.pub
"""

import argparse
import asyncio
from pathlib import Path

from trust.registry import TrustRegistry

from .a2a_util import COMPLETED, REJECTED, Outcome, build_app, log_event, make_card, serve

# What KYB (know-your-business) verification already established, offline.
KYB_DIRECTORY = {
    "BIZ-100245": {"legal_name": "Acme Supply Co.", "approved_scopes": ["trade_payment"]},
    "BIZ-200311": {"legal_name": "Globex Wholesale", "approved_scopes": ["trade_payment", "credit_inquiry"]},
}


def build_registry_app(port: int, log_path: str | None = None):
    registry = TrustRegistry()
    log = lambda kind, **d: log_event(log_path, "registry", kind, **d)  # noqa: E731

    async def register_business(args: dict) -> Outcome:
        bid = args.get("business_id", "")
        kyb = KYB_DIRECTORY.get(bid)
        if kyb is None:
            log("onboard_denied", business_id=bid, reason="not KYB-verified")
            return Outcome(REJECTED, {"reason": "business is not KYB-verified"},
                           f"{bid} is not in the KYB directory")
        scopes = [s for s in args.get("scopes", []) if s in kyb["approved_scopes"]]
        if not scopes:
            log("onboard_denied", business_id=bid, reason="no approved scopes requested")
            return Outcome(REJECTED, {"reason": "no approved scopes"}, "no requested scope is approved")
        credential = registry.register_business(
            bid, kyb["legal_name"], args["agent_public_key"], scopes,
            ttl_seconds=int(args.get("ttl_seconds", 30 * 24 * 3600)))
        log("onboard", business_id=bid, legal_name=kyb["legal_name"], scopes=scopes,
            ttl=credential["claims"]["expires_at"] - credential["claims"]["issued_at"])
        return Outcome(COMPLETED, {"credential": credential})

    async def renew_credential(args: dict) -> Outcome:
        bid = args.get("business_id", "")
        record = registry.record(bid)
        if record is None or record["status"] != "active":
            log("renew_denied", business_id=bid, reason="not active")
            return Outcome(REJECTED, {"reason": "business not active"}, "cannot renew: not active")
        if record["agent_public_key"] != args.get("agent_public_key"):
            log("renew_denied", business_id=bid, reason="key mismatch")
            return Outcome(REJECTED, {"reason": "key mismatch"}, "cannot renew: key is not the registered key")
        kyb = KYB_DIRECTORY[bid]
        credential = registry.register_business(
            bid, record["legal_name"], record["agent_public_key"], kyb["approved_scopes"],
            ttl_seconds=int(args.get("ttl_seconds", 30 * 24 * 3600)))
        log("renew", business_id=bid)
        return Outcome(COMPLETED, {"credential": credential})

    async def get_status(args: dict) -> Outcome:
        return Outcome(COMPLETED, {"status": registry.status(args.get("business_id", ""))})

    async def watchlist_event(args: dict) -> Outcome:
        """An external risk signal. The registry's own policy decides what to do."""
        bid, severity = args.get("business_id", ""), args.get("severity", "low")
        if registry.record(bid) is None:
            return Outcome(REJECTED, {"reason": "unknown business"}, "unknown business")
        if severity == "high":
            registry.revoke(bid)
            log("revoke", business_id=bid, source=args.get("source", "?"), severity=severity)
            return Outcome(COMPLETED, {"action": "revoked"})
        log("flag", business_id=bid, source=args.get("source", "?"), severity=severity)
        return Outcome(COMPLETED, {"action": "flagged"})

    card = make_card(
        "Trust Registry",
        "Verifies businesses (KYB), issues Business Identity Credentials, answers live status.",
        port,
        [("register_business", "Issue a credential to a KYB-verified business"),
         ("renew_credential", "Re-issue a credential for an active business"),
         ("get_status", "Live status: active, revoked or unknown"),
         ("watchlist_event", "Ingest a risk signal; policy may revoke autonomously")],
    )
    app = build_app(card, {
        "register_business": register_business, "renew_credential": renew_credential,
        "get_status": get_status, "watchlist_event": watchlist_event,
    })
    return app, registry


async def run(port: int, key_file: str | None, log_path: str | None) -> None:
    app, registry = build_registry_app(port, log_path)
    if key_file:  # the trust anchor, published out of band for consumers to pin
        Path(key_file).write_text(registry.public_key)
    await serve(app, port)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9101)
    ap.add_argument("--key-file")
    ap.add_argument("--log")
    a = ap.parse_args()
    asyncio.run(run(a.port, a.key_file, a.log))


if __name__ == "__main__":
    main()
