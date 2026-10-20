"""
Phase 1: the trust handshake, in one process.

A trusted Secretary of State connector and a rogue source submit business
formation filings to the Bureau Core. The first two scenarios are the blueprint
walkthrough (docs/SPEC.md, section 4); the rest are the other ways a
submission can go wrong.

Run:  python -m phase1.demo
"""

import copy

from trust import crypto
from trust.audit import SUCCESS
from trust.bureau import BureauCore
from trust.registry import TrustedAgentRegistry
from trust.source_agent import SourceAgent

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def filing(name, filing_id, jurisdiction="CA", formed="2024-03-15"):
    return {"legal_name": name, "state_filing_id": filing_id,
            "formation_date": formed, "jurisdiction": jurisdiction}


def show(title, decision):
    color = GREEN if decision.accepted else RED
    print(f"\n{BOLD}{title}{RESET}")
    print(f"  {color}{decision.status}{RESET}  {DIM}[{decision.check}] {decision.reason}{RESET}")


def main() -> None:
    # Setup: the bureau trusts one connector, for California filings only.
    registry = TrustedAgentRegistry()
    trusted_key = crypto.new_keypair()
    registry.register("SOS-CA-CONNECTOR", crypto.public_key_b64(trusted_key), ["CA"])
    trusted = SourceAgent("SOS-CA-CONNECTOR", trusted_key)
    bureau = BureauCore(registry.lookup)

    # 1. Blueprint walkthrough, trusted ingestion.
    sent = trusted.submit(filing("Acme Logistics LLC", "C4829301"))
    show("1. Trusted agent files Acme Logistics LLC", bureau.submit(sent))

    # 2. Blueprint walkthrough, rejected ingestion.
    rogue = SourceAgent("ROGUE-AGENT-7", crypto.new_keypair())
    show("2. Rogue agent files Shell Corp Inc", bureau.submit(rogue.submit(filing("Shell Corp Inc", "C9999999"))))

    # 3. The rogue claims the trusted agent's identity but cannot sign as it.
    impostor = SourceAgent("SOS-CA-CONNECTOR", crypto.new_keypair())
    show("3. Rogue impersonates the trusted agent", bureau.submit(impostor.submit(filing("Shell Corp Inc", "C9999998"))))

    # 4. A real submission altered in transit.
    tampered = copy.deepcopy(trusted.submit(filing("Birch Freight Inc", "C4829302")))
    tampered["business_filing"]["legal_name"] = "Shell Corp Inc"
    show("4. Filing altered after the agent signed it", bureau.submit(tampered))

    # 5. The exact message from step 1 sent again.
    show("5. Replay of an already accepted submission", bureau.submit(sent))

    # 6. Trusted agent, but a Delaware filing from a California connector.
    show("6. Trusted agent files outside its jurisdiction",
         bureau.submit(trusted.submit(filing("Cedar Holdings LLC", "D1000234", jurisdiction="DE"))))

    # 7. Trusted agent sends a payload that fails validation.
    show("7. Trusted agent sends an invalid formation date",
         bureau.submit(trusted.submit(filing("Dune Supply Co", "C4829303", formed="2024-13-45"))))

    # 8. The same filing, submitted again with a fresh token.
    show("8. Trusted agent files an already ingested filing again",
         bureau.submit(trusted.submit(filing("Acme Logistics LLC", "C4829301"))))

    # 9. The registry revokes the agent; the very next submission is refused.
    registry.revoke("SOS-CA-CONNECTOR")
    show("9. Trusted agent files after being revoked",
         bureau.submit(trusted.submit(filing("Elm Partners LP", "C4829304"))))

    print(f"\n{BOLD}Verified store{RESET}")
    for r in bureau.pipeline.verified_store:
        print(f"  {r['state_filing_id']}  {r['legal_name']} ({r['jurisdiction']}, formed {r['formation_date']})"
              f"  via {r['submitted_by']}")
    print(f"\n{BOLD}Audit log{RESET}")
    for e in bureau.audit.entries:
        color = GREEN if e["status"] == SUCCESS else RED
        print(f"  {color}{e['status']:<30}{RESET} {e['agent_id']:<18} {e['legal_name']}")


if __name__ == "__main__":
    main()
