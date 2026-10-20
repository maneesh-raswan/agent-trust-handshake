"""
Trust Registry: the "bureau" in the middle.

It knows which businesses are real and verified, binds each business to the
public key of the agent allowed to act for it, and issues a signed Business
Identity Credential. It also answers live status checks, so a credential that
was valid yesterday can be revoked today.
"""

import time

from . import crypto

CREDENTIAL_TTL_SECONDS = 30 * 24 * 3600  # 30 days


class TrustRegistry:
    def __init__(self, name: str = "demo-trust-registry"):
        self.name = name
        self._signing_key = crypto.new_keypair()
        self._businesses: dict[str, dict] = {}

    @property
    def public_key(self) -> str:
        """Published once. Every consumer pins this key."""
        return crypto.public_key_b64(self._signing_key)

    def register_business(
        self,
        business_id: str,
        legal_name: str,
        agent_public_key: str,
        scopes: list[str],
        ttl_seconds: int = CREDENTIAL_TTL_SECONDS,
    ) -> dict:
        """Verify a business (assumed done offline, like KYB) and issue its agent a credential."""
        self._businesses[business_id] = {
            "legal_name": legal_name,
            "agent_public_key": agent_public_key,
            "status": "active",
        }
        now = int(time.time())
        claims = {
            "issuer": self.name,
            "business_id": business_id,
            "legal_name": legal_name,
            "agent_public_key": agent_public_key,
            "scopes": scopes,
            "issued_at": now,
            "expires_at": now + ttl_seconds,
        }
        return {"claims": claims, "issuer_signature": crypto.sign(self._signing_key, claims)}

    def record(self, business_id: str) -> dict | None:
        """The registry's own record for a business (None if never registered)."""
        return self._businesses.get(business_id)

    def revoke(self, business_id: str) -> None:
        self._businesses[business_id]["status"] = "revoked"

    def status(self, business_id: str) -> str:
        """Live lookup. In production this is an API call to the registry."""
        record = self._businesses.get(business_id)
        return record["status"] if record else "unknown"
