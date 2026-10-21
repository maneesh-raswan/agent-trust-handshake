"""
Trusted agent registry as an A2A service.

Wraps the core `TrustedAgentRegistry` and adds what a networked registry needs:

  - a vetting gate: only source agents the bureau has vetted offline can register,
    and only for the jurisdictions they were vetted for
  - one key per agent: an agent_id cannot be re-registered under a different key
  - an autonomous risk policy: a high-severity watchlist event (operator-only, needs
    TRUST_ADMIN_TOKEN) revokes an agent immediately, with no human in the loop

Skills: register_agent, lookup_agent, watchlist_event

Run:  python -m phase2.registry_service --port 9101
"""

import argparse
import asyncio

from trust.registry import TrustedAgentRegistry

from .a2a_util import COMPLETED, REJECTED, Outcome, build_app, log_event, make_card, require_admin, serve

# Vetted offline (the bureau's onboarding process); the registry only enforces it.
VETTED_SOURCES = {
    "SOS-CA-CONNECTOR": ["CA"],
    "SOS-NY-CONNECTOR": ["NY"],
}


def build_registry_app(port: int, log_path: str | None = None):
    registry = TrustedAgentRegistry()
    log = lambda kind, **d: log_event(log_path, "registry", kind, **d)  # noqa: E731

    async def register_agent(args: dict) -> Outcome:
        agent_id, key = args.get("agent_id", ""), args.get("public_key", "")
        if agent_id not in VETTED_SOURCES:
            log("register_denied", agent_id=agent_id, reason="not a vetted source")
            return Outcome(REJECTED, {"reason": "not a vetted source"}, f"{agent_id} is not a vetted source")
        existing = registry.lookup(agent_id)
        if existing and existing["public_key"] != key:
            log("register_denied", agent_id=agent_id, reason="already registered under another key")
            return Outcome(REJECTED, {"reason": "key mismatch"}, "agent_id is already registered under another key")
        registry.register(agent_id, key, VETTED_SOURCES[agent_id])
        log("registered", agent_id=agent_id, jurisdictions=VETTED_SOURCES[agent_id])
        return Outcome(COMPLETED, {"jurisdictions": VETTED_SOURCES[agent_id]})

    async def lookup_agent(args: dict) -> Outcome:
        return Outcome(COMPLETED, {"agent": registry.lookup(args.get("agent_id", ""))})

    async def watchlist_event(args: dict) -> Outcome:
        """An external risk signal from the operator. The registry's own policy decides what to do."""
        if (refusal := require_admin(args)):
            log("admin_denied", skill="watchlist_event")
            return refusal
        agent_id, severity = args.get("agent_id", ""), args.get("severity", "low")
        if registry.lookup(agent_id) is None:
            return Outcome(REJECTED, {"reason": "unknown agent"}, "unknown agent")
        if severity == "high":
            registry.revoke(agent_id)
            log("revoked", agent_id=agent_id, source=args.get("source", "?"), severity=severity)
            return Outcome(COMPLETED, {"action": "revoked"})
        log("flagged", agent_id=agent_id, source=args.get("source", "?"), severity=severity)
        return Outcome(COMPLETED, {"action": "flagged"})

    card = make_card(
        "Trusted Agent Registry",
        "Which source agents the bureau trusts: vetted identity, key, jurisdictions, live status.",
        port,
        [("register_agent", "Register a vetted source agent's public key"),
         ("lookup_agent", "Live record for an agent_id: key, jurisdictions, status")],
        # watchlist_event is operator-only and deliberately not advertised
    )
    return build_app(card, {"register_agent": register_agent, "lookup_agent": lookup_agent,
                            "watchlist_event": watchlist_event}), registry


async def run(port: int, log_path: str | None) -> None:
    app, _ = build_registry_app(port, log_path)
    await serve(app, port)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9101)
    ap.add_argument("--log")
    a = ap.parse_args()
    asyncio.run(run(a.port, a.log))


if __name__ == "__main__":
    main()
