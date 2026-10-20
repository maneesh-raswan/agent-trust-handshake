"""
Trusted agent registry: which source agents the bureau trusts.

A record binds an agent_id to the public key it must sign with, the
jurisdictions it may file for, and a live status. Revoking an agent takes
effect on its very next submission.
"""


class TrustedAgentRegistry:
    def __init__(self):
        self._agents: dict[str, dict] = {}

    def register(self, agent_id: str, public_key: str, jurisdictions: list[str]) -> None:
        self._agents[agent_id] = {
            "agent_id": agent_id,
            "public_key": public_key,
            "jurisdictions": list(jurisdictions),
            "status": "active",
        }

    def revoke(self, agent_id: str) -> None:
        self._agents[agent_id]["status"] = "revoked"

    def lookup(self, agent_id: str) -> dict | None:
        record = self._agents.get(agent_id)
        return dict(record) if record else None
