import pytest

from phase3.brain import Turn
from phase3.loop import Done, Tool, run_agent


class Replay:
    """A brain that plays back a fixed list of turns."""

    def __init__(self, turns):
        self.turns, self.seen = list(turns), []

    async def decide(self, system, messages, tools):
        self.seen.append(messages[-1])
        return self.turns.pop(0)


def use(name, **args):
    block = {"type": "tool_use", "id": f"id_{name}", "name": name, "input": args}
    return Turn(tool_calls=[block], stop="tool_use", assistant_content=[block])


async def ok(args):
    return {"echo": args}


async def boom(args):
    raise RuntimeError("tool exploded")


async def stop(args):
    raise Done("all done")


def tools():
    schema = {"type": "object", "properties": {}}
    return [Tool("ok", "d", schema, ok), Tool("boom", "d", schema, boom), Tool("finish", "d", schema, stop)]


def result_of(brain):
    return brain.seen[-1]["content"][0]


async def test_a_hallucinated_tool_is_an_error_to_learn_from_not_a_crash():
    brain = Replay([use("fetch_credit_score"), use("finish")])
    assert await run_agent(brain, "s", "g", tools(), lambda *a, **k: None) == "finished"
    first = brain.seen[1]["content"][0]
    assert first["is_error"] and "no such tool" in first["content"]


async def test_a_tool_that_raises_is_reported_back_and_the_loop_continues():
    brain = Replay([use("boom"), use("ok", x=1), use("finish")])
    assert await run_agent(brain, "s", "g", tools(), lambda *a, **k: None) == "finished"
    assert brain.seen[1]["content"][0]["is_error"]
    assert not brain.seen[2]["content"][0]["is_error"]


async def test_a_refusal_ends_the_run_cleanly():
    brain = Replay([Turn(stop="refusal")])
    events = []
    assert await run_agent(brain, "s", "g", tools(), lambda kind, **d: events.append(kind)) == "refused"
    assert "model_refused" in events


async def test_a_run_that_never_ends_is_cut_off():
    brain = Replay([use("ok")] * 5)
    assert await run_agent(brain, "s", "g", tools(), lambda *a, **k: None, max_steps=3) == "max_steps"


async def test_the_model_talking_without_tools_ends_the_run():
    assert await run_agent(Replay([Turn(text="all done")]), "s", "g", tools(), lambda *a, **k: None) == "ended"
