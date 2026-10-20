"""
Audit logger: records every decision, accepted or rejected, with the reason.

Statuses are the vocabulary of the whole system. SUCCESS and
UNAUTHORIZED_SOURCE_REJECTED come straight from the blueprint walkthrough; the
rest name the other ways a submission can fail.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

SUCCESS = "SUCCESS"
UNAUTHORIZED_SOURCE_REJECTED = "UNAUTHORIZED_SOURCE_REJECTED"  # unknown, revoked or unproven identity
REPLAY_REJECTED = "REPLAY_REJECTED"                            # stale timestamp or reused token
INVALID_PAYLOAD_REJECTED = "INVALID_PAYLOAD_REJECTED"          # malformed envelope or filing
OUT_OF_JURISDICTION_REJECTED = "OUT_OF_JURISDICTION_REJECTED"  # trusted agent, wrong state
DUPLICATE_FILING_REJECTED = "DUPLICATE_FILING_REJECTED"        # already in the verified store


@dataclass
class Decision:
    status: str
    check: str = ""    # which check decided it: shape, identity, signature, freshness, replay, ...
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.status == SUCCESS


class AuditLogger:
    def __init__(self, sink=None):
        self.entries: list[dict] = []
        self._sink = sink  # optional callable(entry), e.g. to persist elsewhere

    def log(self, decision: Decision, envelope) -> dict:
        meta = envelope.get("agent_metadata") if isinstance(envelope, dict) else None
        filing = envelope.get("business_filing") if isinstance(envelope, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        filing = filing if isinstance(filing, dict) else {}
        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "status": decision.status,
            "agent_id": meta.get("agent_id"),
            "legal_name": filing.get("legal_name"),
            "state_filing_id": filing.get("state_filing_id"),
            "check": decision.check,
            "reason": decision.reason,
        }
        self.entries.append(entry)
        if self._sink:
            self._sink(entry)
        return entry
