"""
Agent Authentication Module: is this sender who it claims to be, and is this
submission fresh? Runs before the bureau looks at any filing content.

  1. identity   the agent_id is in the trusted registry
  2. status     and its registry status is active (live, so revocation bites at once)
  3. signature  auth_token verifies with the key on file, over this exact filing
  4. freshness  timestamp is within the allowed window
  5. replay     this auth_token has not been accepted before
"""

from datetime import datetime, timezone

from . import crypto
from .audit import REPLAY_REJECTED, UNAUTHORIZED_SOURCE_REJECTED, Decision

MAX_CLOCK_SKEW_SECONDS = 300


class AgentAuthenticator:
    def __init__(self):
        self._accepted_tokens: set[str] = set()

    def verify(self, envelope: dict, agent: dict | None) -> Decision | None:
        """Return a rejection Decision, or None if the sender is authentic and the message fresh."""
        meta, filing = envelope["agent_metadata"], envelope["business_filing"]

        if agent is None:
            return Decision(UNAUTHORIZED_SOURCE_REJECTED, "identity", "agent is not in the trusted registry")
        if agent["status"] != "active":
            return Decision(UNAUTHORIZED_SOURCE_REJECTED, "status", f"agent status is '{agent['status']}'")

        signed_fields = {"agent_id": meta["agent_id"], "timestamp": meta["timestamp"], "business_filing": filing}
        if not crypto.verify(agent["public_key"], signed_fields, meta["auth_token"]):
            return Decision(UNAUTHORIZED_SOURCE_REJECTED, "signature",
                            "auth_token invalid: wrong key, or the filing was altered")

        try:
            sent = datetime.fromisoformat(meta["timestamp"].replace("Z", "+00:00"))
            age = abs((datetime.now(timezone.utc) - sent).total_seconds())
        except (ValueError, AttributeError):
            return Decision(REPLAY_REJECTED, "freshness", "timestamp is not ISO-8601")
        if age > MAX_CLOCK_SKEW_SECONDS:
            return Decision(REPLAY_REJECTED, "freshness", "timestamp outside the allowed window")

        if meta["auth_token"] in self._accepted_tokens:
            return Decision(REPLAY_REJECTED, "replay", "auth_token already used")
        return None

    def mark_used(self, envelope: dict) -> None:
        self._accepted_tokens.add(envelope["agent_metadata"]["auth_token"])
