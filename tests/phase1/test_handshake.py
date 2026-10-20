import copy

import pytest

from trust import crypto
from trust.audit import (DUPLICATE_FILING_REJECTED, INVALID_PAYLOAD_REJECTED, OUT_OF_JURISDICTION_REJECTED,
                         REPLAY_REJECTED, SUCCESS, UNAUTHORIZED_SOURCE_REJECTED)
from trust.bureau import BureauCore
from trust.registry import TrustedAgentRegistry
from trust.source_agent import SourceAgent


def filing(name="Acme Logistics LLC", filing_id="C4829301", jurisdiction="CA", formed="2024-03-15"):
    return {"legal_name": name, "state_filing_id": filing_id,
            "formation_date": formed, "jurisdiction": jurisdiction}


@pytest.fixture
def world():
    registry = TrustedAgentRegistry()
    key = crypto.new_keypair()
    registry.register("SOS-CA", crypto.public_key_b64(key), ["CA"])
    return registry, SourceAgent("SOS-CA", key), BureauCore(registry.lookup)


def test_trusted_agent_filing_is_ingested_and_audited(world):
    _, trusted, bureau = world
    d = bureau.submit(trusted.submit(filing()))
    assert d.status == SUCCESS
    assert [r["legal_name"] for r in bureau.pipeline.verified_store] == ["Acme Logistics LLC"]
    assert bureau.audit.entries[-1]["status"] == SUCCESS


def test_rogue_agent_is_rejected_and_nothing_is_stored(world):
    _, _, bureau = world
    rogue = SourceAgent("ROGUE-1", crypto.new_keypair())
    d = bureau.submit(rogue.submit(filing("Shell Corp Inc", "C9999999")))
    assert d.status == UNAUTHORIZED_SOURCE_REJECTED and d.check == "identity"
    assert bureau.pipeline.verified_store == []
    assert bureau.audit.entries[-1]["status"] == UNAUTHORIZED_SOURCE_REJECTED


def test_impersonating_a_trusted_agent_fails_the_signature_challenge(world):
    _, _, bureau = world
    impostor = SourceAgent("SOS-CA", crypto.new_keypair())
    d = bureau.submit(impostor.submit(filing("Shell Corp Inc", "C9999998")))
    assert d.status == UNAUTHORIZED_SOURCE_REJECTED and d.check == "signature"


def test_filing_altered_in_transit_is_rejected(world):
    _, trusted, bureau = world
    env = copy.deepcopy(trusted.submit(filing()))
    env["business_filing"]["legal_name"] = "Shell Corp Inc"
    d = bureau.submit(env)
    assert d.status == UNAUTHORIZED_SOURCE_REJECTED and d.check == "signature"


def test_replayed_submission_is_rejected(world):
    _, trusted, bureau = world
    env = trusted.submit(filing())
    assert bureau.submit(env).accepted
    d = bureau.submit(env)
    assert d.status == REPLAY_REJECTED and d.check == "replay"


def test_stale_timestamp_is_rejected(world):
    _, trusted, bureau = world
    env = trusted.submit(filing())
    env["agent_metadata"]["timestamp"] = "2020-01-01T00:00:00Z"
    # Re-sign so only freshness can fail.
    signed = {"agent_id": "SOS-CA", "timestamp": "2020-01-01T00:00:00Z", "business_filing": env["business_filing"]}
    env["agent_metadata"]["auth_token"] = crypto.sign(trusted._private_key, signed)
    d = bureau.submit(env)
    assert d.status == REPLAY_REJECTED and d.check == "freshness"


def test_filing_outside_the_agents_jurisdiction_is_rejected(world):
    _, trusted, bureau = world
    d = bureau.submit(trusted.submit(filing(jurisdiction="DE", filing_id="D1000234")))
    assert d.status == OUT_OF_JURISDICTION_REJECTED


@pytest.mark.parametrize("bad", [
    {"formed": "2024-13-45"}, {"formed": "2999-01-01"}, {"jurisdiction": "California"}, {"filing_id": "x"},
])
def test_invalid_payload_is_rejected(world, bad):
    _, trusted, bureau = world
    d = bureau.submit(trusted.submit(filing(**bad)))
    assert d.status == INVALID_PAYLOAD_REJECTED and bureau.pipeline.verified_store == []


def test_duplicate_filing_is_rejected(world):
    _, trusted, bureau = world
    assert bureau.submit(trusted.submit(filing())).accepted
    assert bureau.submit(trusted.submit(filing())).status == DUPLICATE_FILING_REJECTED


def test_revoked_agent_is_rejected_immediately(world):
    registry, trusted, bureau = world
    assert bureau.submit(trusted.submit(filing())).accepted
    registry.revoke("SOS-CA")
    d = bureau.submit(trusted.submit(filing("Birch Freight Inc", "C4829302")))
    assert d.status == UNAUTHORIZED_SOURCE_REJECTED and d.check == "status"
    assert len(bureau.pipeline.verified_store) == 1


@pytest.mark.parametrize("junk", [None, "x", {}, {"agent_metadata": None}, {"agent_metadata": {}, "business_filing": {}}])
def test_malformed_envelopes_are_rejected_not_crashed(world, junk):
    _, _, bureau = world
    assert bureau.submit(junk).status == INVALID_PAYLOAD_REJECTED


def test_every_submission_is_audited_with_a_reason(world):
    _, trusted, bureau = world
    bureau.submit(trusted.submit(filing()))
    bureau.submit(SourceAgent("ROGUE-1", crypto.new_keypair()).submit(filing("Shell Corp Inc", "C9999999")))
    assert [e["status"] for e in bureau.audit.entries] == [SUCCESS, UNAUTHORIZED_SOURCE_REJECTED]
    assert all(e["reason"] for e in bureau.audit.entries)
