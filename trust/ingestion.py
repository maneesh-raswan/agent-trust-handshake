"""
Ingestion Pipeline: validates Secretary of State business formation payloads.

The schema (docs/SPEC.md, section 5):

    {
      "agent_metadata": {"agent_id": str, "auth_token": str, "timestamp": ISO-8601},
      "business_filing": {"legal_name": str, "state_filing_id": str,
                          "formation_date": "YYYY-MM-DD", "jurisdiction": str}
    }

Only filings from authenticated agents reach this stage.
"""

import re
from datetime import date

from .audit import (DUPLICATE_FILING_REJECTED, INVALID_PAYLOAD_REJECTED,
                    OUT_OF_JURISDICTION_REJECTED, Decision)

ENVELOPE_FIELDS = {
    "agent_metadata": ("agent_id", "auth_token", "timestamp"),
    "business_filing": ("legal_name", "state_filing_id", "formation_date", "jurisdiction"),
}


def check_shape(envelope) -> Decision | None:
    """Is this even a well-formed envelope? Checked first so later stages can rely on it."""
    if not isinstance(envelope, dict):
        return Decision(INVALID_PAYLOAD_REJECTED, "shape", "envelope is not an object")
    for section, fields in ENVELOPE_FIELDS.items():
        block = envelope.get(section)
        if not isinstance(block, dict) or not all(isinstance(block.get(f), str) and block[f] for f in fields):
            return Decision(INVALID_PAYLOAD_REJECTED, "shape", f"'{section}' is missing or incomplete")
    return None


def check_filing(filing: dict) -> Decision | None:
    if not re.fullmatch(r"[A-Z]{2}", filing["jurisdiction"]):
        return Decision(INVALID_PAYLOAD_REJECTED, "payload", "jurisdiction must be a two-letter state code")
    if not re.fullmatch(r"[A-Z0-9-]{4,20}", filing["state_filing_id"]):
        return Decision(INVALID_PAYLOAD_REJECTED, "payload", "state_filing_id format is invalid")
    try:
        formed = date.fromisoformat(filing["formation_date"])
    except ValueError:
        return Decision(INVALID_PAYLOAD_REJECTED, "payload", "formation_date is not a valid YYYY-MM-DD date")
    if formed > date.today():
        return Decision(INVALID_PAYLOAD_REJECTED, "payload", "formation_date is in the future")
    return None


class IngestionPipeline:
    def __init__(self, store=None):
        self.verified_store: list[dict] = []
        self._store = store  # optional callable(record), e.g. to persist elsewhere
        self._filed: set[tuple[str, str]] = set()

    def ingest(self, filing: dict, agent: dict, agent_id: str) -> Decision | None:
        """Validate and store. Returns a rejection Decision, or None once the filing is stored."""
        if (bad := check_filing(filing)):
            return bad
        if filing["jurisdiction"] not in agent["jurisdictions"]:
            return Decision(OUT_OF_JURISDICTION_REJECTED, "jurisdiction",
                            f"agent may file for {agent['jurisdictions']}, not '{filing['jurisdiction']}'")
        key = (filing["jurisdiction"], filing["state_filing_id"])
        if key in self._filed:
            return Decision(DUPLICATE_FILING_REJECTED, "duplicate", "filing is already in the verified store")
        self._filed.add(key)
        record = {**filing, "submitted_by": agent_id}
        self.verified_store.append(record)
        if self._store:
            self._store(record)
        return None
