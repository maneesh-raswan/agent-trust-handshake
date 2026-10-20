"""
Source agent: simulates a data provider, such as a Secretary of State API
connector, submitting business formation filings.

The auth_token is an Ed25519 signature over {agent_id, timestamp, business_filing}.
It proves the sender holds the key the registry has on file for agent_id, and it
binds the token to this exact filing.
"""

from datetime import datetime, timezone

from . import crypto


def now_iso() -> str:
    # Microseconds, because Ed25519 signatures are deterministic: two submissions of the
    # same filing in the same instant would otherwise carry an identical auth_token.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class SourceAgent:
    def __init__(self, agent_id: str, private_key):
        self.agent_id = agent_id
        self._private_key = private_key

    @property
    def public_key(self) -> str:
        return crypto.public_key_b64(self._private_key)

    def submit(self, business_filing: dict) -> dict:
        """Package a filing in the ingestion schema (see docs/SPEC.md, section 5)."""
        signed_fields = {
            "agent_id": self.agent_id,
            "timestamp": now_iso(),
            "business_filing": business_filing,
        }
        return {
            "agent_metadata": {
                "agent_id": self.agent_id,
                "auth_token": crypto.sign(self._private_key, signed_fields),
                "timestamp": signed_fields["timestamp"],
            },
            "business_filing": business_filing,
        }
