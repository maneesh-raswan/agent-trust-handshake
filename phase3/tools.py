"""
What an LLM-driven source agent can do. Three kinds of tool:

  MCP      discovered by listing the feed server's tools: nothing here names them
  A2A      discover_agent reads an Agent Card; call_agent uses a skill it advertises
  local    the few things only code can do: sign with the agent's key, and end the run

The model never holds a private key. `submit_filing` (trusted) signs a filing it did not write; the
rogue's `sign_and_submit` signs whatever it likes under any claimed identity, because a hostile agent
could. Neither can make the bureau accept anything: that decision is not here.
"""

import json

from phase2.a2a_util import COMPLETED, STATE_NAMES, call, fetch_card, log_event
from trust.source_agent import SourceAgent

from .loop import Done, Tool

URL = {"type": "string", "description": "Base URL of the agent, e.g. http://127.0.0.1:9101"}


def obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props)}


class SourceKit:
    def __init__(self, role: str, agent: SourceAgent, feed, log_path: str | None):
        self.role, self.agent, self.feed = role, agent, feed
        self.log = lambda kind, **d: log_event(log_path, role, kind, **d)
        self.filings: dict[str, dict] = {}      # pending filings the agent has seen, by id
        self.last_envelope: dict | None = None  # for the rogue's replay
        self.summary = ""

    # --- shared ----------------------------------------------------------------

    async def discover_agent(self, args: dict) -> dict:
        card = await fetch_card(args["url"])
        self.log("discovered", agent=card["name"], skills=[s["id"] for s in card.get("skills", [])])
        return {"name": card["name"], "description": card["description"],
                "skills": [{"id": s["id"], "description": s["description"]} for s in card.get("skills", [])]}

    async def call_agent(self, args: dict) -> dict:
        skill, params = args["skill"], args.get("args") or {}
        if isinstance(params, str):  # models sometimes send the object as a JSON string
            params = json.loads(params)
        out = await call(args["url"], skill, params)
        self.log("a2a_call", skill=skill, state=STATE_NAMES.get(out.state, out.state))
        return {"state": STATE_NAMES.get(out.state, out.state), "text": out.text, "data": out.data}

    async def finish(self, args: dict) -> str:
        self.summary = str(args.get("summary", ""))
        raise Done(self.summary)

    def mcp_tool(self, spec: dict) -> Tool:
        async def handler(args: dict):
            result = await self.feed.call(spec["name"], **args)
            for filing in result.get("filings", []):
                self.filings[filing["state_filing_id"]] = {k: v for k, v in filing.items() if k != "status"}
            return result
        return Tool(spec["name"], f"[MCP] {spec['description']}", spec["input_schema"], handler)

    async def _send(self, agent: SourceAgent, bureau_url: str, filing: dict) -> dict:
        """Sign, send, and log what the bureau decided."""
        envelope = agent.submit(filing)
        self.last_envelope = envelope
        self.log("submit", agent_id=agent.agent_id, legal_name=filing.get("legal_name"),
                 state_filing_id=filing.get("state_filing_id"))
        out = await call(bureau_url, "submit_filing", {"envelope": envelope})
        status = out.data.get("status", "ERROR")
        answer = {"status": status, "check": out.data.get("check", ""), "reason": out.text}
        if status == "SUCCESS":
            self.log("ingested" if self.role == "trusted" else "slipped_through",
                     state_filing_id=filing.get("state_filing_id"), status=status)
        else:
            self.log("refused" if self.role == "trusted" else "blocked",
                     state_filing_id=filing.get("state_filing_id"), status=status)
        return answer

    # --- trusted ---------------------------------------------------------------

    async def register_my_key(self, args: dict) -> dict:
        out = await call(args["registry_url"], "register_agent",
                         {"agent_id": self.agent.agent_id, "public_key": self.agent.public_key})
        if out.state == COMPLETED:
            self.log("registered", agent_id=self.agent.agent_id, jurisdictions=out.data.get("jurisdictions"))
        else:
            self.log("register_refused", agent_id=self.agent.agent_id)
        return {"state": STATE_NAMES.get(out.state, out.state), "text": out.text, "data": out.data}

    async def submit_filing(self, args: dict) -> dict:
        filing = self.filings.get(args["state_filing_id"])
        if filing is None:
            return {"error": "unknown state_filing_id: list your pending filings first"}
        return await self._send(self.agent, args["bureau_url"], filing)

    async def escalate(self, args: dict) -> str:
        self.log("escalated", reason=str(args.get("reason", ""))[:200])
        return "escalated to a human operator"

    # --- rogue -----------------------------------------------------------------

    async def sign_and_submit(self, args: dict) -> dict:
        claimed = SourceAgent(args["agent_id"], self.agent._private_key)  # own key, any claimed identity
        filing = {k: args[k] for k in ("legal_name", "state_filing_id", "formation_date", "jurisdiction")}
        return await self._send(claimed, args["bureau_url"], filing)

    async def replay_last(self, args: dict) -> dict:
        if not self.last_envelope:
            return {"error": "nothing sent yet"}
        self.log("replay", state_filing_id=self.last_envelope["business_filing"].get("state_filing_id"))
        out = await call(args["bureau_url"], "submit_filing", {"envelope": self.last_envelope})
        return {"status": out.data.get("status", "ERROR"), "reason": out.text}

    async def my_public_key(self, args: dict) -> dict:
        return {"agent_id": self.agent.agent_id, "public_key": self.agent.public_key}

    # --- assembly --------------------------------------------------------------

    async def build_tools(self) -> list[Tool]:
        tools = [self.mcp_tool(spec) for spec in await self.feed.list_tools()]
        tools += [
            Tool("discover_agent", "[A2A] Read another agent's Agent Card: what it is and the skills it offers.",
                 obj(url=URL), self.discover_agent),
            Tool("call_agent", "[A2A] Call a skill an agent offers. args is an object of that skill's arguments.",
                 {"type": "object", "properties": {"url": URL, "skill": {"type": "string"},
                                                    "args": {"type": "object"}}, "required": ["url", "skill"]},
                 self.call_agent),
            Tool("finish", "End your run with a short summary.", obj(summary={"type": "string"}), self.finish),
        ]
        if self.role == "trusted":
            tools += [
                Tool("register_my_key", "Register your public key with the trusted agent registry.",
                     obj(registry_url=URL), self.register_my_key),
                Tool("submit_filing", "Sign and submit one of your pending filings to the Bureau Core.",
                     obj(bureau_url=URL, state_filing_id={"type": "string"}), self.submit_filing),
                Tool("escalate", "Flag a problem for a human operator.", obj(reason={"type": "string"}),
                     self.escalate),
            ]
        else:
            fields = {k: {"type": "string"} for k in
                      ("agent_id", "legal_name", "state_filing_id", "formation_date", "jurisdiction")}
            tools += [
                Tool("sign_and_submit", "Sign a filing with your own key under any agent_id you choose and "
                     "submit it to the Bureau Core. Any content in any field.",
                     obj(bureau_url=URL, **fields), self.sign_and_submit),
                Tool("replay_last", "Resend your most recent submission unchanged.", obj(bureau_url=URL),
                     self.replay_last),
                Tool("my_public_key", "Show your own agent_id and public key.", {"type": "object", "properties": {}},
                     self.my_public_key),
            ]
        return tools
