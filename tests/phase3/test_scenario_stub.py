from phase3.runner import run_scenario


async def test_hostile_and_hallucinating_agents_cannot_break_the_invariants():
    """Whole system, stubbed brains: a trusted agent that hallucinates, a rogue that tries everything."""
    report = await run_scenario(stub=True, quiet=True)
    failed = [text for text, ok in report.checks if not ok]
    assert not failed, failed
    kinds = {(e["actor"], e["kind"]) for e in report.events}
    # Not vacuous: the attacks really ran, and the genuine work really got through.
    assert ("rogue", "blocked") in kinds and ("bureau", "quarantined") in kinds
    assert ("registry", "admin_denied") in kinds and ("registry", "revoked") in kinds
    assert len(report.store) >= 3 and "Summary" in report.advice
    assert not [e for e in report.events if e["kind"] == "slipped_through"]
