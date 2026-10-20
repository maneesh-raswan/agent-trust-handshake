import asyncio

import pytest

from phase2.a2a_util import COMPLETED, REJECTED, call, serve, wait_ready
from phase2.registry_service import build_registry_app
from phase2.runner import free_port
from trust import crypto


@pytest.fixture
async def registry():
    port = free_port()
    app, core = build_registry_app(port)
    task = asyncio.create_task(serve(app, port))
    url = f"http://127.0.0.1:{port}"
    await wait_ready(url)
    yield url, core
    task.cancel()


def onboarding(business_id="BIZ-100245", scopes=("trade_payment",), key=None):
    key = key or crypto.new_keypair()
    return key, {"business_id": business_id, "agent_public_key": crypto.public_key_b64(key),
                 "scopes": list(scopes)}


async def test_kyb_verified_business_gets_a_credential_signed_by_the_registry(registry):
    url, core = registry
    _, args = onboarding()
    out = await call(url, "register_business", args)
    cred = out.data["credential"]
    assert out.state == COMPLETED
    assert crypto.verify(core.public_key, cred["claims"], cred["issuer_signature"])


async def test_unvetted_business_cannot_onboard(registry):
    url, _ = registry
    _, args = onboarding(business_id="BIZ-666666")
    assert (await call(url, "register_business", args)).state == REJECTED


async def test_granted_scopes_are_capped_at_what_kyb_approved(registry):
    url, _ = registry
    _, args = onboarding(scopes=("trade_payment", "credit_inquiry"))
    out = await call(url, "register_business", args)
    assert out.data["credential"]["claims"]["scopes"] == ["trade_payment"]


async def test_renewal_requires_the_registered_key(registry):
    url, _ = registry
    key, args = onboarding()
    await call(url, "register_business", args)
    _, other = onboarding()
    assert (await call(url, "renew_credential", other)).state == REJECTED
    assert (await call(url, "renew_credential", args)).state == COMPLETED


async def test_high_severity_watchlist_event_revokes_autonomously(registry):
    url, _ = registry
    _, args = onboarding()
    await call(url, "register_business", args)
    assert (await call(url, "get_status", {"business_id": "BIZ-100245"})).data["status"] == "active"
    await call(url, "watchlist_event", {"business_id": "BIZ-100245", "severity": "low"})
    assert (await call(url, "get_status", {"business_id": "BIZ-100245"})).data["status"] == "active"
    await call(url, "watchlist_event", {"business_id": "BIZ-100245", "severity": "high"})
    assert (await call(url, "get_status", {"business_id": "BIZ-100245"})).data["status"] == "revoked"
    assert (await call(url, "renew_credential", args)).state == REJECTED
