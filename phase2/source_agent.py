"""
Source agent as an autonomous A2A service. One module plays either role from
the blueprint: the trusted Secretary of State connector or the bad actor.

TRUSTED  (--mode trusted)
  1. generates its own key and registers it with the trusted agent registry (A2A)
  2. reads pending filings from its feed (MCP tools) and submits each to the Bureau Core (A2A)
  3. decides what to do with each answer:
       SUCCESS / duplicate          mark the filing ingested
       out of jurisdiction, invalid mark it rejected and carry on
       UNAUTHORIZED (e.g. revoked)  stop sending, hold every remaining filing
       registry unreachable         back off and retry

ROGUE  (--mode rogue)
  Has no registration and no way to get one. Submits fake filings under its own
  agent_id. When the bureau starts blocking it, it adapts: it claims the trusted
  agent's identity instead. When that fails too, it gives up.

Skills: get_report (what this agent has done so far)

Run:  python -m phase2.source_agent --mode trusted --registry-url http://127.0.0.1:9101 \
          --bureau-url http://127.0.0.1:9102
"""

import argparse
import asyncio

from trust import crypto
from trust.source_agent import SourceAgent

from . import mcp_util
from .a2a_util import COMPLETED, FAILED, Outcome, build_app, call, log_event, make_card, serve, wait_ready

TRUSTED_ID = "SOS-CA-CONNECTOR"
ROGUE_ID = "ROGUE-AGENT-7"


class SourceAgentService:
    def __init__(self, mode: str, agent_id: str, registry_url: str, bureau_url: str, feed,
                 interval: float, log_path: str | None):
        self.mode, self.registry_url, self.bureau_url, self.feed, self.interval = mode, registry_url, bureau_url, feed, interval
        self.agent = SourceAgent(agent_id, crypto.new_keypair())
        self.actor = "trusted" if mode == "trusted" else "rogue"
        self.log = lambda kind, **d: log_event(log_path, self.actor, kind, **d)
        self.halted = False
        self.done = False
        self.results: dict[str, str] = {}
        self._impersonating = False  # rogue only: has it switched to claiming the trusted identity?
        self._impersonation_failures = 0

    async def run_forever(self) -> None:
        try:
            if self.mode == "trusted":
                await self.register()
            for filing in (await self.feed.call("list_pending_filings"))["filings"]:
                filing = {k: v for k, v in filing.items() if k != "status"}
                if self.halted:
                    await self._record(filing, "held")
                else:
                    await self._record(filing, await (self.deliver if self.mode == "trusted" else self.attack)(filing))
                    await asyncio.sleep(self.interval)
        except Exception as exc:
            self.log("crashed", reason=str(exc))
        finally:
            self.done = True
            self.log("finished")

    async def register(self) -> None:
        out = await call(self.registry_url, "register_agent",
                         {"agent_id": self.agent.agent_id, "public_key": self.agent.public_key})
        if out.state != COMPLETED:
            raise RuntimeError(f"registration refused: {out.text}")
        self.log("registered", agent_id=self.agent.agent_id, jurisdictions=out.data["jurisdictions"])

    async def _record(self, filing: dict, status: str) -> None:
        self.results[filing["state_filing_id"]] = status
        await self.feed.call("mark_filing", state_filing_id=filing["state_filing_id"], status=status)

    async def _submit(self, filing: dict, agent: SourceAgent | None = None) -> Outcome:
        agent = agent or self.agent
        self.log("submit", agent_id=agent.agent_id, legal_name=filing["legal_name"],
                 state_filing_id=filing["state_filing_id"])
        return await call(self.bureau_url, "submit_filing", {"envelope": agent.submit(filing)})

    # --- trusted ----------------------------------------------------------------

    async def deliver(self, filing: dict) -> str:
        for attempt in range(4):
            try:
                out = await self._submit(filing)
            except Exception as exc:
                self.log("retry", state_filing_id=filing["state_filing_id"], reason=f"bureau unreachable: {exc}")
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if out.state == FAILED:  # e.g. the bureau could not reach the registry and failed closed
                self.log("retry", state_filing_id=filing["state_filing_id"], reason=out.text)
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            status = out.data["status"]
            if status in ("SUCCESS", "DUPLICATE_FILING_REJECTED"):
                self.log("ingested", state_filing_id=filing["state_filing_id"], status=status)
                return "ingested"
            if status == "UNAUTHORIZED_SOURCE_REJECTED":
                self.halted = True
                self.log("halt", reason=f"bureau no longer accepts this identity ({out.data['check']}): "
                                        "holding all remaining filings")
                return "held"
            self.log("refused", state_filing_id=filing["state_filing_id"], status=status, reason=out.text)
            return f"rejected:{status}"
        return "gave_up"

    # --- rogue ------------------------------------------------------------------

    async def attack(self, filing: dict) -> str:
        if self._impersonating:
            out = await self._submit(filing, SourceAgent(TRUSTED_ID, self.agent._private_key))
        else:
            out = await self._submit(filing)
        status = out.data.get("status", "?")
        self.log("blocked" if out.state != COMPLETED else "slipped_through",
                 state_filing_id=filing["state_filing_id"], status=status)
        if status == "QUARANTINED_SOURCE_REJECTED" and not self._impersonating:
            self._impersonating = True
            self.log("adapt", reason=f"{self.agent.agent_id} is blocked: claiming the trusted agent's identity")
        elif self._impersonating and status == "UNAUTHORIZED_SOURCE_REJECTED":
            self._impersonation_failures += 1
            if self._impersonation_failures >= 2:
                self.halted = True
                self.log("give_up", reason="every route is blocked: stopping")
        return f"rejected:{status}"

    async def get_report(self, args: dict) -> Outcome:
        return Outcome(COMPLETED, {"done": self.done, "halted": self.halted, "results": self.results})


async def run(port: int, mode: str, agent_id: str, registry_url: str, bureau_url: str,
              interval: float, log_path: str | None) -> None:
    await wait_ready(registry_url)
    await wait_ready(bureau_url)
    async with mcp_util.launch("phase2.mcp_sos_feed", {"FEED": mode}) as feed:
        service = SourceAgentService(mode, agent_id, registry_url, bureau_url, feed, interval, log_path)
        card = make_card(
            "Trusted Source Agent" if mode == "trusted" else "Source Agent",
            "Secretary of State connector submitting business formation filings.", port,
            [("get_report", "What this agent has submitted and how each was decided")])
        work = asyncio.create_task(service.run_forever())
        try:
            await serve(build_app(card, {"get_report": service.get_report}), port)
        finally:
            work.cancel()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["trusted", "rogue"], required=True)
    ap.add_argument("--port", type=int, default=9103)
    ap.add_argument("--agent-id")
    ap.add_argument("--registry-url", required=True)
    ap.add_argument("--bureau-url", required=True)
    ap.add_argument("--interval", type=float, default=0.7)
    ap.add_argument("--log")
    a = ap.parse_args()
    agent_id = a.agent_id or (TRUSTED_ID if a.mode == "trusted" else ROGUE_ID)
    asyncio.run(run(a.port, a.mode, agent_id, a.registry_url, a.bureau_url, a.interval, a.log))


if __name__ == "__main__":
    main()
