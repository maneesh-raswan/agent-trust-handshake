"""
Bureau Advisor: the optional LLM layer next to the Bureau Core.

It explains rejections, summarizes the audit log and points out patterns. That is all it can do:
it reads a sanitized digest (see digest.py), has no tools, and has no path to the store, the
registry or any decision. Operator-only, like the audit log it reads.

Skills: summarize_audit (operator-only)

Run:  python -m phase3.advisor_service --port 9105 --bureau-url http://127.0.0.1:9102 [--stub]
"""

import argparse
import asyncio
import json
import os

from phase2.a2a_util import COMPLETED, FAILED, Outcome, build_app, call, make_card, require_admin, serve

from . import prompts
from .digest import build_digest


class Advisor:
    def __init__(self, bureau_url: str, brain, input_log: str | None):
        self.bureau_url, self.brain, self.input_log = bureau_url, brain, input_log

    async def summarize_audit(self, args: dict) -> Outcome:
        if (refusal := require_admin(args)):
            return refusal
        audit = await call(self.bureau_url, "get_audit", {"admin_token": os.environ.get("TRUST_ADMIN_TOKEN", "")})
        if audit.state != COMPLETED:
            return Outcome(FAILED, {}, "could not read the audit log")
        digest = build_digest(audit.data["entries"])
        prompt = "Audit digest follows.\nDIGEST:\n" + json.dumps(digest, indent=1)
        if self.input_log:  # exactly what the model is shown, so a test can prove nothing else was
            with open(self.input_log, "a") as f:
                f.write(prompt + "\n")
        turn = await self.brain.decide(prompts.ADVISOR_SYSTEM, [{"role": "user", "content": prompt}], [])
        return Outcome(COMPLETED, {"advice": turn.text, "entries_reviewed": len(digest["entries"])}, "advice ready")


def build_advisor_app(port: int, advisor: Advisor):
    card = make_card("Bureau Advisor", "Advisory only: explains rejections and summarizes the audit log. "
                     "Cannot change any decision.", port,
                     [("summarize_audit", "Operator-only: summarize the audit log and flag patterns")])
    return build_app(card, {"summarize_audit": advisor.summarize_audit})


async def run(port: int, bureau_url: str, stub: bool, input_log: str | None) -> None:
    if stub:
        from .stub_brain import StubAdvisorBrain
        brain = StubAdvisorBrain()
    else:
        from .brain import AnthropicBrain
        brain = AnthropicBrain("advisor", fallbacks=True)
    await serve(build_advisor_app(port, Advisor(bureau_url, brain, input_log)), port)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9105)
    ap.add_argument("--bureau-url", required=True)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--input-log")
    ap.add_argument("--log")  # accepted for symmetry with the other services
    a = ap.parse_args()
    asyncio.run(run(a.port, a.bureau_url, a.stub, a.input_log))


if __name__ == "__main__":
    main()
