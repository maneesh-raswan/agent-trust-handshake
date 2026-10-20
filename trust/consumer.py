"""
Consumer Agent: acts for the receiving platform (for example, a bureau ingesting
contributed data). Before ingesting anything it answers four questions:

  1. Is this identity real?          (issuer signature, expiry, live status)
  2. Is the sender that identity?    (signature with the credential's key)
  3. Is this message fresh and new?  (timestamp window, nonce replay check)
  4. Is it allowed to send this?     (scope check)

Only then does it ingest and return a signed receipt. Every decision is logged
with the reason, so a human or an auditor can replay why it happened.

Trust decisions here are deterministic code, not model judgment. An LLM can
help produce or interpret the data, but it should never be the thing that
decides whether a counterparty is who it claims to be.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import crypto

MAX_CLOCK_SKEW_SECONDS = 300


@dataclass
class Decision:
    accepted: bool
    checks: list = field(default_factory=list)  # (check name, passed, detail)
    receipt: dict | None = None

    @property
    def reason(self) -> str:
        failed = [c for c in self.checks if not c[1]]
        return failed[0][2] if failed else "all checks passed"


class ConsumerAgent:
    def __init__(self, registry_public_key: str, registry_status, ledger_path: Path | None = None,
                 ledger_sink=None):
        self._registry_public_key = registry_public_key  # pinned trust anchor
        self._registry_status = registry_status          # live lookup function
        self._ledger_path = Path(ledger_path) if ledger_path else None
        self._ledger_sink = ledger_sink  # optional callable(entry); replaces the file ledger
        self._seen_nonces: set[str] = set()
        self._signing_key = crypto.new_keypair()

    @property
    def public_key(self) -> str:
        return crypto.public_key_b64(self._signing_key)

    def receive(self, envelope: dict) -> Decision:
        decision = Decision(accepted=False)
        now = int(time.time())

        def check(name, passed, detail):
            decision.checks.append((name, passed, detail))
            return passed

        try:
            cred = envelope["credential"]
            claims = cred["claims"]
            body = envelope["body"]
            well_formed = all(k in claims for k in ("business_id", "agent_public_key", "scopes", "expires_at")) \
                and all(k in body for k in ("business_id", "payload", "nonce", "sent_at")) \
                and "issuer_signature" in cred and "signature" in envelope
        except (KeyError, TypeError):
            well_formed = False
        if not check("format", well_formed,
                     "envelope is well formed" if well_formed else "malformed envelope"):
            return decision

        # 1. Is this identity real?
        issued_by_registry = crypto.verify(self._registry_public_key, claims, cred["issuer_signature"])
        if not check("issuer", issued_by_registry,
                     "credential signed by trusted registry" if issued_by_registry
                     else "credential NOT signed by trusted registry (forged or self-issued)"):
            return decision
        if not check("expiry", claims["expires_at"] > now,
                     "credential within validity window" if claims["expires_at"] > now
                     else "credential expired"):
            return decision
        status = self._registry_status(claims["business_id"])
        if not check("status", status == "active", f"registry status is '{status}'"):
            return decision

        # 2. Is the sender that identity?
        sig_ok = crypto.verify(claims["agent_public_key"], body, envelope["signature"])
        bound = body["business_id"] == claims["business_id"]
        if not check("signature", sig_ok and bound,
                     "signed by the agent key bound to this business" if sig_ok and bound
                     else "signature invalid: payload altered or wrong key"):
            return decision

        # 3. Is this message fresh and new?
        fresh = abs(now - body["sent_at"]) <= MAX_CLOCK_SKEW_SECONDS
        if not check("freshness", fresh, "sent within allowed window" if fresh else "message too old"):
            return decision
        new = body["nonce"] not in self._seen_nonces
        if not check("replay", new, "first time seen" if new else "duplicate nonce: replay attempt"):
            return decision

        # 4. Is it allowed to send this?
        txn_type = body["payload"].get("type")
        in_scope = txn_type in claims["scopes"]
        if not check("scope", in_scope,
                     f"authorized for '{txn_type}'" if in_scope
                     else f"not authorized for '{txn_type}'"):
            return decision

        # Ingest and settle.
        self._seen_nonces.add(body["nonce"])
        payload_hash = crypto.sha256(body["payload"])
        self._append_ledger(claims, body, payload_hash, now)
        receipt_claims = {
            "txn_id": body["payload"]["txn_id"],
            "business_id": claims["business_id"],
            "payload_hash": payload_hash,
            "status": "settled",
            "accepted_at": now,
        }
        decision.receipt = {"claims": receipt_claims, "signature": crypto.sign(self._signing_key, receipt_claims)}
        decision.accepted = True
        return decision

    def _append_ledger(self, claims, body, payload_hash, now):
        entry = {
            "accepted_at": now,
            "business_id": claims["business_id"],
            "legal_name": claims["legal_name"],
            "payload_hash": payload_hash,
            "payload": body["payload"],
        }
        if self._ledger_sink:
            self._ledger_sink(entry)
            return
        with self._ledger_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
