import asyncio

import pytest

from phase2.a2a_util import COMPLETED, REJECTED, call, serve, wait_ready
from phase2.registry_service import build_registry_app
from phase2.runner import free_port
from trust import crypto


@pytest.fixture
async def registry():
    port = free_port()
    app, _ = build_registry_app(port)
    task = asyncio.create_task(serve(app, port))
    url = f"http://127.0.0.1:{port}"
    await wait_ready(url)
    yield url
    task.cancel()


def key_args(agent_id="SOS-CA-CONNECTOR", key=None):
    return {"agent_id": agent_id, "public_key": crypto.public_key_b64(key or crypto.new_keypair())}


async def test_vetted_source_can_register_with_its_jurisdictions(registry):
    out = await call(registry, "register_agent", key_args())
    assert out.state == COMPLETED and out.data["jurisdictions"] == ["CA"]
    record = (await call(registry, "lookup_agent", {"agent_id": "SOS-CA-CONNECTOR"})).data["agent"]
    assert record["status"] == "active" and record["jurisdictions"] == ["CA"]


async def test_unvetted_source_cannot_register(registry):
    assert (await call(registry, "register_agent", key_args("ROGUE-AGENT-7"))).state == REJECTED
    assert (await call(registry, "lookup_agent", {"agent_id": "ROGUE-AGENT-7"})).data["agent"] is None


async def test_an_agent_id_cannot_be_taken_over_with_a_second_key(registry):
    args = key_args()
    await call(registry, "register_agent", args)
    assert (await call(registry, "register_agent", key_args())).state == REJECTED
    assert (await call(registry, "register_agent", args)).state == COMPLETED  # same key is fine


async def test_high_severity_watchlist_event_revokes_autonomously(registry):
    await call(registry, "register_agent", key_args())
    status = lambda: call(registry, "lookup_agent", {"agent_id": "SOS-CA-CONNECTOR"})  # noqa: E731
    await call(registry, "watchlist_event", {"agent_id": "SOS-CA-CONNECTOR", "severity": "low"})
    assert (await status()).data["agent"]["status"] == "active"
    await call(registry, "watchlist_event", {"agent_id": "SOS-CA-CONNECTOR", "severity": "high"})
    assert (await status()).data["agent"]["status"] == "revoked"
