"""
Bureau Core: the ingestion and verification engine.

  submit(envelope):  shape  ->  Agent Authentication  ->  Ingestion Pipeline  ->  Audit Logger

Nothing reaches the verified store unless the sender is authenticated first,
and every submission, accepted or not, is audited with its reason.

Trust decisions here are deterministic code, not model judgment. An LLM can
help produce or interpret filings, but it should never decide whether a
source is who it claims to be.
"""

from .audit import SUCCESS, AuditLogger, Decision
from .auth import AgentAuthenticator
from .ingestion import IngestionPipeline, check_shape


class BureauCore:
    def __init__(self, lookup_agent, store=None, audit_sink=None):
        """lookup_agent(agent_id) -> registry record or None (the trusted agent registry)."""
        self._lookup = lookup_agent
        self.auth = AgentAuthenticator()
        self.pipeline = IngestionPipeline(store)
        self.audit = AuditLogger(audit_sink)

    def submit(self, envelope) -> Decision:
        decision = self._decide(envelope)
        self.audit.log(decision, envelope)
        return decision

    def _decide(self, envelope) -> Decision:
        if (bad := check_shape(envelope)):
            return bad
        agent_id = envelope["agent_metadata"]["agent_id"]
        agent = self._lookup(agent_id)
        if (bad := self.auth.verify(envelope, agent)):
            return bad
        if (bad := self.pipeline.ingest(envelope["business_filing"], agent, agent_id)):
            return bad
        self.auth.mark_used(envelope)
        return Decision(SUCCESS, "all", "filing ingested into the verified store")
