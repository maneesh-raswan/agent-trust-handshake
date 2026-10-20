"""
Agent-to-agent trust handshake demo.

A Producer Agent (a data provider) sends a trade payment record to a Consumer
Agent (the platform ingesting it). The consumer verifies the producer's business
identity before it ingests anything, then returns a signed settlement receipt.

Run:  python demo.py
"""

import copy
import tempfile
from pathlib import Path

from trust import crypto
from trust.consumer import ConsumerAgent
from trust.producer import ProducerAgent
from trust.registry import TrustRegistry

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def trade_payment(txn_id: str, amount: float, txn_type: str = "trade_payment") -> dict:
    return {
        "txn_id": txn_id,
        "type": txn_type,
        "debtor_business_id": "BIZ-778812",
        "amount": amount,
        "currency": "USD",
        "terms": "NET30",
        "days_beyond_terms": 0,
    }


def show(title: str, decision) -> None:
    print(f"\n{BOLD}{title}{RESET}")
    for name, passed, detail in decision.checks:
        mark = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
        print(f"  {mark}  {name:<10} {DIM}{detail}{RESET}")
    if decision.accepted:
        r = decision.receipt["claims"]
        print(f"  {GREEN}INGESTED{RESET}  receipt {r['txn_id']} status={r['status']} "
              f"hash={r['payload_hash'][:12]}...")
    else:
        print(f"  {RED}REJECTED{RESET}  {decision.reason}")


def main() -> None:
    ledger = Path(tempfile.mkdtemp()) / "ledger.jsonl"

    # Setup: the registry verifies Acme Supply and binds its agent's key.
    registry = TrustRegistry()
    acme_key = crypto.new_keypair()
    acme_credential = registry.register_business(
        business_id="BIZ-100245",
        legal_name="Acme Supply Co.",
        agent_public_key=crypto.public_key_b64(acme_key),
        scopes=["trade_payment"],
    )
    producer = ProducerAgent(acme_key, acme_credential)
    consumer = ConsumerAgent(registry.public_key, registry.status, ledger)

    # 1. Happy path: verified producer sends a valid record.
    first = producer.send(trade_payment("TXN-0001", 12500.00))
    d = consumer.receive(first)
    show("1. Verified producer sends a trade payment", d)
    settled = ProducerAgent.verify_receipt(d.receipt, consumer.public_key)
    print(f"  Producer verifies signed receipt: {GREEN if settled else RED}{settled}{RESET}")

    # 2. Impostor: claims to be Acme, but issues its own credential.
    impostor_key = crypto.new_keypair()
    fake_claims = dict(acme_credential["claims"], agent_public_key=crypto.public_key_b64(impostor_key))
    fake_credential = {"claims": fake_claims, "issuer_signature": crypto.sign(impostor_key, fake_claims)}
    impostor = ProducerAgent(impostor_key, fake_credential)
    show("2. Impostor claims Acme's identity with a self-issued credential",
         consumer.receive(impostor.send(trade_payment("TXN-0002", 99000.00))))

    # 3. Tampered in transit: amount changed after the producer signed it.
    tampered = copy.deepcopy(producer.send(trade_payment("TXN-0003", 4200.00)))
    tampered["body"]["payload"]["amount"] = 420000.00
    show("3. Record altered in transit (amount 4,200 changed to 420,000)", consumer.receive(tampered))

    # 4. Replay: the exact message from step 1 is sent again.
    show("4. Replay of an already ingested message", consumer.receive(first))

    # 5. Out of scope: Acme is real, but not authorized to send credit inquiries.
    show("5. Verified producer sends a data type it is not authorized for",
         consumer.receive(producer.send(trade_payment("TXN-0005", 0, txn_type="credit_inquiry"))))

    # 6. Revoked: the registry revokes Acme. Its credential is still unexpired,
    #    but the live status check stops it.
    registry.revoke("BIZ-100245")
    show("6. Producer sends after its business was revoked in the registry",
         consumer.receive(producer.send(trade_payment("TXN-0006", 8800.00))))

    print(f"\n{BOLD}Ledger{RESET} {DIM}({ledger}){RESET}")
    print("  " + ledger.read_text().strip().replace("\n", "\n  "))


if __name__ == "__main__":
    main()
