import asyncio

import pytest

from phase2.a2a_util import COMPLETED, FAILED, REJECTED, Outcome, build_app, call, make_card, serve, wait_ready
from phase2.runner import free_port


@pytest.fixture
async def echo_agent():
    port = free_port()

    async def echo(args):
        return Outcome(COMPLETED, {"echo": args}, "ok")

    async def refuse(args):
        return Outcome(REJECTED, {"why": "no"}, "refused")

    async def boom(args):
        raise RuntimeError("bug in skill")

    card = make_card("echo", "test agent", port, [("echo", "e"), ("refuse", "r"), ("boom", "b")])
    task = asyncio.create_task(serve(build_app(card, {"echo": echo, "refuse": refuse, "boom": boom}), port))
    url = f"http://127.0.0.1:{port}"
    await wait_ready(url)
    yield url
    task.cancel()


async def test_numbers_survive_the_wire_exactly(echo_agent):
    # Signatures are over canonical JSON, so 12500.0 must stay a float and an
    # epoch timestamp must stay an int. Protobuf doubles would break both.
    out = await call(echo_agent, "echo", {"amount": 12500.0, "expires_at": 1792440103})
    assert out.state == COMPLETED
    assert out.data["echo"] == {"amount": 12500.0, "expires_at": 1792440103}
    assert isinstance(out.data["echo"]["expires_at"], int)
    assert repr(out.data["echo"]["amount"]) == "12500.0"


async def test_task_states_carry_the_decision(echo_agent):
    assert (await call(echo_agent, "refuse")).state == REJECTED


async def test_unknown_skill_and_skill_bugs_fail_without_killing_the_service(echo_agent):
    assert (await call(echo_agent, "nope")).state == FAILED
    assert (await call(echo_agent, "boom")).state == FAILED
    assert (await call(echo_agent, "echo", {"still": "alive"})).state == COMPLETED
