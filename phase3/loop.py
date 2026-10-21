"""
The agent loop: show the model its tools, run whatever it asks for, hand back the results,
repeat until it stops. The loop is dumb on purpose. Every decision about what to do next
belongs to the brain; every safety decision belongs to the Bureau Core.

Things a brain can get wrong are handled here, not trusted away: a tool that does not
exist, arguments that do not fit, a handler that raises, an endless run.
"""

import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from .brain import Brain

MAX_RESULT_CHARS = 6000


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], Awaitable[object]]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


class Done(Exception):
    """Raised by the finish tool to end the loop cleanly."""


async def run_agent(brain: Brain, system: str, goal: str, tools: list[Tool], log, max_steps: int = 40) -> str:
    """Run until the brain finishes, refuses, stops calling tools, or runs out of steps."""
    by_name = {t.name: t for t in tools}
    specs = [t.spec() for t in tools]
    messages: list = [{"role": "user", "content": goal}]

    for _ in range(max_steps):
        turn = await brain.decide(system, messages, specs)
        if turn.text.strip():
            log("thought", text=turn.text.strip().replace("\n", " ")[:240])
        if turn.stop == "refusal":
            log("model_refused", reason="the model declined to continue")
            return "refused"
        if not turn.tool_calls:
            return "ended" if turn.stop != "max_tokens" else "truncated"

        messages.append({"role": "assistant", "content": turn.assistant_content})
        results, finished = [], False
        for call in turn.tool_calls:
            tool = by_name.get(call["name"])
            log("act", tool=call["name"], args=_brief(call["input"]))
            if tool is None:  # a hallucinated tool is an error to learn from, not a crash
                out, is_error = f"no such tool '{call['name']}'. Available: {sorted(by_name)}", True
            else:
                try:
                    out, is_error = await tool.handler(call["input"]), False
                except Done as done:
                    out, is_error, finished = str(done) or "finished", False, True
                except Exception as exc:
                    out, is_error = f"{type(exc).__name__}: {exc}", True
            text = out if isinstance(out, str) else json.dumps(out, default=str)
            results.append({"type": "tool_result", "tool_use_id": call["id"],
                            "content": text[:MAX_RESULT_CHARS], "is_error": is_error})
        messages.append({"role": "user", "content": results})
        if finished:
            return "finished"
    return "max_steps"


def _brief(args: dict) -> str:
    text = json.dumps(args, default=str)
    return text if len(text) <= 160 else text[:157] + "..."
