import asyncio

import pytest

from phase2.a2a_util import COMPLETED, FAILED, REJECTED, serve, wait_ready
from phase2.bureau_core import QUARANTINE_AFTER, BureauService
from phase2.registry_service import build_registry_app
from phase2.runner import free_port
from trust import crypto
from trust.source_agent import SourceAgent


class FakeStore:
    """Stands in for the MCP store tools, so these tests need no subprocess."""

    def __init__(self):
        self.calls = []

    async def call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return {}


def filing(n=1):
    return {"legal_name": f"Co {n}", "state_filing_id": f"C100{n:04d}",
            "formation_date": "2024-03-15", "jurisdiction": "CA"}


@pytest.fixture
async def world():
    port = free_port()
    app, registry = build_registry_app(port)
    task = asyncio.create_task(serve(app, port))
    url = f"http://127.0.0.1:{port}"
    await wait_ready(url)
    key = crypto.new_keypair()
    registry.register("SOS-CA-CONNECTOR", crypto.public_key_b64(key), ["CA"])
    store = FakeStore()
    yield BureauService(url, store, None), SourceAgent("SOS-CA-CONNECTOR", key), store
    task.cancel()


async def test_store_and_audit_reads_are_operator_only(world, monkeypatch):
    bureau, _, store = world
    monkeypatch.setenv("TRUST_ADMIN_TOKEN", "test-admin")
    assert (await bureau.get_store({})).state == REJECTED
    assert (await bureau.get_audit({"admin_token": "guess"})).state == REJECTED
    assert (await bureau.get_audit({"admin_token": "test-admin"})).state == COMPLETED


async def test_accepted_filing_goes_to_the_store_and_the_audit_log(world):
    bureau, trusted, store = world
    out = await bureau.submit_filing({"envelope": trusted.submit(filing())})
    assert out.state == COMPLETED and out.data["status"] == "SUCCESS"
    assert [name for name, _ in store.calls] == ["store_filing", "audit_append"]


async def test_repeat_unknown_source_is_quarantined(world):
    bureau, _, store = world
    rogue = SourceAgent("ROGUE-1", crypto.new_keypair())
    statuses = [(await bureau.submit_filing({"envelope": rogue.submit(filing(i))})).data["status"]
                for i in range(QUARANTINE_AFTER + 1)]
    assert statuses == ["UNAUTHORIZED_SOURCE_REJECTED"] * QUARANTINE_AFTER + ["QUARANTINED_SOURCE_REJECTED"]
    assert not any(name == "store_filing" for name, _ in store.calls)


async def test_impersonating_a_trusted_agent_never_gets_it_quarantined(world):
    bureau, trusted, _ = world
    impostor = SourceAgent("SOS-CA-CONNECTOR", crypto.new_keypair())
    for i in range(QUARANTINE_AFTER + 2):
        out = await bureau.submit_filing({"envelope": impostor.submit(filing(i))})
        assert out.data["check"] == "signature"
    assert (await bureau.submit_filing({"envelope": trusted.submit(filing(99))})).data["status"] == "SUCCESS"


async def test_registry_down_means_fail_closed():
    store = FakeStore()
    bureau = BureauService(f"http://127.0.0.1:{free_port()}", store, None)
    trusted = SourceAgent("SOS-CA-CONNECTOR", crypto.new_keypair())
    out = await bureau.submit_filing({"envelope": trusted.submit(filing())})
    assert out.state == FAILED and store.calls == []


async def test_garbage_input_is_rejected_not_crashed(world):
    bureau, _, _ = world
    for junk in (None, "x", {"agent_metadata": 5}, {"agent_metadata": {"agent_id": ["a"]}}):
        out = await bureau.submit_filing({"envelope": junk})
        assert out.state == REJECTED and out.data["status"] == "INVALID_PAYLOAD_REJECTED"
