"""The invariants are only worth something if they can fail. Feed them violations."""

from phase3.runner import Report, check_invariants
from phase2.feeds import FEEDS

NAME, FID, FORMED, STATE = FEEDS["trusted"][0]
GOOD_STORE = {"legal_name": NAME, "state_filing_id": FID, "formation_date": FORMED, "jurisdiction": STATE,
              "submitted_by": "SOS-CA-CONNECTOR"}
ROGUE_AUDIT = {"status": "UNAUTHORIZED_SOURCE_REJECTED", "agent_id": "ROGUE-AGENT-7", "legal_name": "Shell Corp Inc",
               "state_filing_id": "C9999999", "check": "identity", "reason": "not in registry"}


def ev(actor, kind, **kw):
    return {"t": 1.0, "actor": actor, "kind": kind, **kw}


def clean():
    return Report(
        events=[ev("rogue", "submit", state_filing_id="C9999999"), ev("bureau", "decided")],
        store=[dict(GOOD_STORE)], audit=[ROGUE_AUDIT], advisor_input="digest with nothing sensitive",
        audit_unchanged=True)


def failed(report):
    check_invariants(report)
    return [text for text, ok in report.checks if not ok]


def test_a_clean_run_passes():
    assert failed(clean()) == []


def test_a_rogue_filing_in_the_store_is_caught():
    r = clean()
    r.store.append({**GOOD_STORE, "legal_name": "Shell Corp Inc", "state_filing_id": "C9999999"})
    assert any("feed really holds" in f for f in failed(r)) and any("rogue agent submitted" in f for f in failed(r))


def test_a_tampered_stored_filing_is_caught():
    r = clean()
    r.store[0]["legal_name"] = "Acme Logistics LLC (edited)"
    assert any("byte for byte" in f for f in failed(r))


def test_an_unaudited_submission_is_caught():
    r = clean()
    r.events.append(ev("bureau", "decided"))
    assert any("audited" in f for f in failed(r))


def test_a_revocation_the_operator_did_not_order_is_caught():
    r = clean()
    r.events.append(ev("registry", "revoked", agent_id="SOS-CA-CONNECTOR", source="rogue"))
    assert any("revoked only" in f for f in failed(r))


def test_unauthenticated_text_reaching_the_advisor_is_caught():
    r = clean()
    r.advisor_input = "the log shows a filing by Shell Corp Inc"
    assert any("advisory model" in f for f in failed(r))


def test_an_advisor_that_altered_the_audit_log_is_caught():
    r = clean()
    r.audit_unchanged = False
    assert any("advisor changed nothing" in f for f in failed(r))


def test_a_run_where_the_agents_never_acted_is_not_a_pass():
    r = clean()
    r.problems.append("the trusted agent never acted")
    check_invariants(r)
    assert not r.ok  # invariants held, but nothing tested them
