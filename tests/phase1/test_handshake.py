import copy

import pytest

from trust import crypto
from trust.consumer import ConsumerAgent
from trust.producer import ProducerAgent
from trust.registry import TrustRegistry


def txn(txn_id="TXN-1", txn_type="trade_payment", amount=100.0):
    return {"txn_id": txn_id, "type": txn_type, "amount": amount}


@pytest.fixture
def setup(tmp_path):
    registry = TrustRegistry()
    key = crypto.new_keypair()
    cred = registry.register_business("BIZ-1", "Acme", crypto.public_key_b64(key), ["trade_payment"])
    producer = ProducerAgent(key, cred)
    consumer = ConsumerAgent(registry.public_key, registry.status, tmp_path / "ledger.jsonl")
    return registry, producer, consumer, cred, tmp_path / "ledger.jsonl"


def test_trusted_producer_is_ingested_and_settled(setup):
    _, producer, consumer, _, ledger = setup
    d = consumer.receive(producer.send(txn()))
    assert d.accepted
    assert ProducerAgent.verify_receipt(d.receipt, consumer.public_key)
    assert len(ledger.read_text().splitlines()) == 1


def test_self_issued_credential_is_rejected(setup):
    _, _, consumer, cred, _ = setup
    fake_key = crypto.new_keypair()
    claims = dict(cred["claims"], agent_public_key=crypto.public_key_b64(fake_key))
    fake = ProducerAgent(fake_key, {"claims": claims, "issuer_signature": crypto.sign(fake_key, claims)})
    d = consumer.receive(fake.send(txn()))
    assert not d.accepted and d.checks[-1][0] == "issuer"


def test_stolen_credential_without_key_is_rejected(setup):
    _, _, consumer, cred, _ = setup
    thief = ProducerAgent(crypto.new_keypair(), cred)  # real credential, wrong private key
    d = consumer.receive(thief.send(txn()))
    assert not d.accepted and d.checks[-1][0] == "signature"


def test_tampered_payload_is_rejected(setup):
    _, producer, consumer, _, _ = setup
    env = copy.deepcopy(producer.send(txn()))
    env["body"]["payload"]["amount"] = 999999
    d = consumer.receive(env)
    assert not d.accepted and d.checks[-1][0] == "signature"


def test_replay_is_rejected(setup):
    _, producer, consumer, _, _ = setup
    env = producer.send(txn())
    assert consumer.receive(env).accepted
    d = consumer.receive(env)
    assert not d.accepted and d.checks[-1][0] == "replay"


def test_out_of_scope_is_rejected(setup):
    _, producer, consumer, _, _ = setup
    d = consumer.receive(producer.send(txn(txn_type="credit_inquiry")))
    assert not d.accepted and d.checks[-1][0] == "scope"


def test_revoked_business_is_rejected(setup):
    registry, producer, consumer, _, ledger = setup
    registry.revoke("BIZ-1")
    d = consumer.receive(producer.send(txn()))
    assert not d.accepted and d.checks[-1][0] == "status"
    assert not ledger.exists()


def test_expired_credential_is_rejected(setup, monkeypatch):
    _, producer, consumer, cred, _ = setup
    import trust.consumer as c
    future = cred["claims"]["expires_at"] + 1
    monkeypatch.setattr(c.time, "time", lambda: future)
    env = producer.send(txn())
    env["body"]["sent_at"] = future  # keep it fresh so only expiry can fail
    d = consumer.receive(env)
    assert not d.accepted and d.checks[-1][0] == "expiry"


def test_stale_message_is_rejected(setup, monkeypatch):
    _, producer, consumer, _, _ = setup
    import trust.consumer as c
    env = producer.send(txn())
    real = c.time.time()
    monkeypatch.setattr(c.time, "time", lambda: real + 3600)
    d = consumer.receive(env)
    assert not d.accepted and d.checks[-1][0] == "freshness"


def test_unknown_business_is_rejected(setup):
    registry, producer, consumer, _, _ = setup
    registry._businesses.clear()
    d = consumer.receive(producer.send(txn()))
    assert not d.accepted and d.checks[-1][0] == "status"


def test_malformed_envelope_is_rejected_not_crashed(setup):
    _, _, consumer, _, _ = setup
    for bad in ({}, {"credential": None}, {"credential": {"claims": {}}, "body": {}}):
        d = consumer.receive(bad)
        assert not d.accepted and d.checks[-1][0] == "format"


def test_forged_receipt_fails_verification(setup):
    _, producer, consumer, _, _ = setup
    d = consumer.receive(producer.send(txn()))
    forged = copy.deepcopy(d.receipt)
    forged["claims"]["status"] = "refunded"
    assert not ProducerAgent.verify_receipt(forged, consumer.public_key)
