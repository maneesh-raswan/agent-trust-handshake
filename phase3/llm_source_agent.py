"""
An LLM-driven source agent: the brain of phase 3's trusted connector or red-team rogue.

Unlike phase 2's source agent there is no hard-coded sequence. The process gives its brain the tools
listed by its MCP feed, the A2A skills it can discover, and a goal, then runs the loop.

Run:  python -m phase3.llm_source_agent --role trusted --registry-url ... --bureau-url ... [--stub]
"""

import argparse
import asyncio

from phase2 import mcp_util
from phase2.a2a_util import log_event, wait_ready
from trust import crypto
from trust.source_agent import SourceAgent

from . import prompts
from .loop import run_agent
from .tools import SourceKit

IDS = {"trusted": "SOS-CA-CONNECTOR", "rogue": "ROGUE-AGENT-7"}
MAX_STEPS = {"trusted": 60, "rogue": 30}


def make_brain(role: str, stub: bool, registry_url: str, bureau_url: str):
    if stub:
        from .stub_brain import rogue_brain, trusted_brain
        return (trusted_brain if role == "trusted" else rogue_brain)(registry_url, bureau_url)
    from .brain import AnthropicBrain
    return AnthropicBrain(role, fallbacks=(role == "trusted"))


async def run(role: str, registry_url: str, bureau_url: str, stub: bool, log_path: str | None) -> None:
    log = lambda kind, **d: log_event(log_path, role, kind, **d)  # noqa: E731
    await wait_ready(registry_url)
    await wait_ready(bureau_url)
    brain = make_brain(role, stub, registry_url, bureau_url)
    system, goal = ((prompts.TRUSTED_SYSTEM, prompts.TRUSTED_GOAL) if role == "trusted"
                    else (prompts.ROGUE_SYSTEM, prompts.ROGUE_GOAL))
    async with mcp_util.launch("phase2.mcp_sos_feed", {"FEED": role}) as feed:
        kit = SourceKit(role, SourceAgent(IDS[role], crypto.new_keypair()), feed, log_path)
        try:
            outcome = await run_agent(brain, system, goal.format(registry_url=registry_url, bureau_url=bureau_url),
                                      await kit.build_tools(), log, MAX_STEPS[role])
        except Exception as exc:  # the brain or the network failed: say so, never hang the run
            outcome = f"error: {type(exc).__name__}: {exc}"[:200]
        log("finished", outcome=outcome, summary=kit.summary[:300])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["trusted", "rogue"], required=True)
    ap.add_argument("--registry-url", required=True)
    ap.add_argument("--bureau-url", required=True)
    ap.add_argument("--stub", action="store_true", help="use the deterministic stand-in brain (no API key)")
    ap.add_argument("--log")
    a = ap.parse_args()
    asyncio.run(run(a.role, a.registry_url, a.bureau_url, a.stub, a.log))


if __name__ == "__main__":
    main()
