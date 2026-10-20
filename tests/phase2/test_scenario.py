from phase2.runner import run_scenario


async def test_autonomous_scenario_holds_every_invariant():
    """End to end: three A2A services, two MCP tool servers, one adversary."""
    report = await run_scenario(quiet=True)
    failed = [text for text, passed in report.checks if not passed]
    assert not failed, failed
    assert len(report.ledger) >= 4
