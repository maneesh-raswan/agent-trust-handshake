"""
Autonomous scenario runner.

Starts the Registry, Consumer and Producer as three separate A2A services (the
Consumer and Producer each start their own MCP tool server), then steps back.
Nothing here drives the honest flow: the producer onboards, sends, renews, and
halts on its own. The runner only

  - plays an adversary with a network tap (forged identity, tampering, replay,
    an unvetted business trying to onboard)
  - injects one external risk signal (a watchlist hit) into the Registry
  - narrates the shared event log as it happens
  - checks, at the end, that the invariants of the system held

Run:  python -m phase2.runner          (or: make phase2-demo)
Exit code is non-zero if any invariant fails.
"""

import asyncio
import copy
import json
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from trust import crypto
from trust.producer import ProducerAgent

from .a2a_util import call, wait_ready

GREEN, RED, YELLOW, CYAN, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m")
ACTOR_COLOR = {"registry": CYAN, "consumer": YELLOW, "producer": GREEN, "adversary": RED, "runner": DIM}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Report:
    events: list = field(default_factory=list)
    ledger: list = field(default_factory=list)
    producer: dict = field(default_factory=dict)
    consumer_stats: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)  # (description, passed)

    @property
    def ok(self) -> bool:
        return all(passed for _, passed in self.checks)


# --- narration -------------------------------------------------------------

def describe(e: dict) -> str:
    k = e["kind"]
    g = e.get
    return {
        "onboard": lambda: f"issued credential to {g('legal_name')} ({g('business_id')}), scopes={g('scopes')}, ttl={g('ttl')}s",
        "onboard_denied": lambda: f"REFUSED onboarding for {g('business_id')}: {g('reason')}",
        "renew": lambda: f"renewed credential for {g('business_id')}",
        "renew_denied": lambda: f"REFUSED renewal for {g('business_id')}: {g('reason')}",
        "revoke": lambda: f"REVOKED {g('business_id')} (source={g('source')}, severity={g('severity')}) automatically",
        "flag": lambda: f"flagged {g('business_id')} (severity={g('severity')})",
        "onboarded": lambda: f"onboarded as {g('business_id')}, granted scopes {g('granted')}",
        "learned_consumer": lambda: f"read Agent Card of '{g('agent')}', learned its receipt key",
        "send": lambda: f"sending {g('txn_id')} ({g('type')}, {g('amount'):,.2f})",
        "settled": lambda: f"{g('txn_id')} settled: receipt signature and payload hash verified",
        "bad_receipt": lambda: f"{g('txn_id')} receipt did NOT verify",
        "credential_expired": lambda: f"{g('txn_id')}: consumer says credential expired, renewing",
        "renewed": lambda: f"got a fresh credential, resending",
        "refused": lambda: f"{g('txn_id')} refused ({g('check')}): {g('reason')}",
        "halt": lambda: f"HALT: {g('reason')}",
        "renewal_refused": lambda: f"registry refused renewal: {g('reason')}",
        "retry": lambda: f"{g('txn_id')} retrying: {g('reason')}",
        "crashed": lambda: f"CRASHED: {g('reason')}",
        "finished": lambda: "finished its run",
        "accepted": lambda: f"ACCEPTED {g('txn_id')} from {g('business_id')}, wrote to ledger via MCP",
        "rejected": lambda: f"REJECTED {g('txn_id')} from {g('business_id')} at '{g('check')}': {g('reason')}",
        "input_required": lambda: f"{g('txn_id')} from {g('business_id')}: credential expired, asked sender to renew",
        "quarantine": lambda: f"QUARANTINED sender key {g('key_prefix')}... after {g('strikes')} forged credentials",
        "failed_closed": lambda: f"{g('txn_id')}: {g('reason')}; failing closed",
        "attack": lambda: g("note"),
        "note": lambda: g("note"),
    }.get(k, lambda: json.dumps({a: b for a, b in e.items() if a not in ("t", "actor", "kind")}))()


def render(e: dict, t0: float) -> str:
    color = ACTOR_COLOR.get(e["actor"], "")
    return f"{DIM}+{e['t'] - t0:5.1f}s{RESET} {color}{e['actor']:<9}{RESET} {describe(e)}"


# --- orchestration -----------------------------------------------------------

class Tail:
    """Reads new lines from the shared event log and prints them in order."""

    def __init__(self, path: Path, t0: float, quiet: bool):
        self.path, self.t0, self.quiet, self.pos, self.events = path, t0, quiet, 0, []

    def poll(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open() as f:
            f.seek(self.pos)
            chunk = f.read()
            self.pos = f.tell()
        new = []
        for line in chunk.splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue  # partial line, will not happen with whole-line appends; be safe
            new.append(e)
            if not self.quiet:
                print(render(e, self.t0))
        self.events += new
        return new

    def count(self, actor: str, kind: str) -> int:
        return sum(1 for e in self.events if e["actor"] == actor and e["kind"] == kind)


def note(log: Path, actor: str, text: str) -> None:
    from .a2a_util import log_event
    log_event(str(log), actor, "attack" if actor == "adversary" else "note", note=text)


async def adversary(urls: dict, log: Path, wire: Path, tail: Tail) -> None:
    consumer, registry = urls["consumer"], urls["registry"]

    await asyncio.sleep(0.8)
    note(log, "adversary", "Shadow Corp (BIZ-666666) tries to onboard with the registry")
    out = await call(registry, "register_business", {
        "business_id": "BIZ-666666", "agent_public_key": "x", "scopes": ["trade_payment"]})
    note(log, "adversary", f"registry answered: {out.text}")

    # An impostor who claims Acme's identity with a credential it signed itself.
    note(log, "adversary", "Impostor presents a self-issued credential claiming to be Acme, four times")
    fake_key = crypto.new_keypair()
    real = await _wait_for_wire(wire, tail)
    claims = dict(real["envelope"]["credential"]["claims"], agent_public_key=crypto.public_key_b64(fake_key))
    impostor = ProducerAgent(fake_key, {"claims": claims, "issuer_signature": crypto.sign(fake_key, claims)})
    for i in range(4):
        await call(consumer, "submit_transaction", {"envelope": impostor.send(
            {"txn_id": f"FAKE-{i}", "type": "trade_payment", "amount": 99000.0})})
        await asyncio.sleep(0.2)

    # Network tap: tamper with, then replay, a legitimate settled message.
    while tail.count("producer", "settled") < 2:
        await asyncio.sleep(0.1)
    settled_txn = next(e["txn_id"] for e in tail.events if e["actor"] == "producer" and e["kind"] == "settled")
    captured = next(w for w in _read_wire(wire) if w["txn_id"] == settled_txn)["envelope"]

    note(log, "adversary", f"Tap captured {settled_txn}: altering the amount in transit")
    tampered = copy.deepcopy(captured)
    tampered["body"]["payload"]["amount"] = 9_999_999.0
    await call(consumer, "submit_transaction", {"envelope": tampered})

    note(log, "adversary", f"Replaying the exact settled message {settled_txn}")
    await call(consumer, "submit_transaction", {"envelope": captured})

    # External risk signal: the registry's own policy decides what to do with it.
    while tail.count("producer", "settled") < 6:
        await asyncio.sleep(0.1)
    note(log, "runner", "external risk signal: watchlist hit on BIZ-100245 sent to the registry")
    await call(registry, "watchlist_event", {
        "business_id": "BIZ-100245", "source": "sanctions-watchlist", "severity": "high"})


def _read_wire(wire: Path) -> list[dict]:
    if not wire.exists():
        return []
    return [json.loads(line) for line in wire.read_text().splitlines() if line]


async def _wait_for_wire(wire: Path, tail: Tail) -> dict:
    while not _read_wire(wire):
        await asyncio.sleep(0.1)
    return _read_wire(wire)[0]


async def run_scenario(quiet: bool = False, interval: float = 0.7, timeout: float = 60.0) -> Report:
    run_dir = Path(tempfile.mkdtemp(prefix="trust-run-"))
    log, wire, ledger, key_file = (run_dir / "events.jsonl", run_dir / "wire.jsonl",
                                   run_dir / "ledger.jsonl", run_dir / "registry.pub")
    ports = {n: free_port() for n in ("registry", "consumer", "producer")}
    urls = {n: f"http://127.0.0.1:{p}" for n, p in ports.items()}
    t0 = time.time()
    tail = Tail(log, t0, quiet)
    procs: list[subprocess.Popen] = []

    def spawn(module: str, *args: str) -> None:
        out = open(run_dir / f"{module.split('.')[-1]}.out", "w")
        procs.append(subprocess.Popen([sys.executable, "-m", module, *args], stdout=out, stderr=subprocess.STDOUT))

    report = Report()
    try:
        if not quiet:
            print(f"{BOLD}Phase 2: autonomous agent trust over A2A + MCP{RESET}  {DIM}(run dir {run_dir}){RESET}\n")
        spawn("phase2.registry_service", "--port", str(ports["registry"]), "--key-file", str(key_file), "--log", str(log))
        await wait_ready(urls["registry"])
        spawn("phase2.consumer_service", "--port", str(ports["consumer"]), "--registry-url", urls["registry"],
              "--registry-key-file", str(key_file), "--ledger", str(ledger), "--log", str(log))
        await wait_ready(urls["consumer"])
        note(log, "runner", "registry and consumer are up; starting the producer and then stepping back")
        spawn("phase2.producer_service", "--port", str(ports["producer"]), "--registry-url", urls["registry"],
              "--consumer-url", urls["consumer"], "--interval", str(interval), "--log", str(log), "--wire", str(wire))
        await wait_ready(urls["producer"])

        attack = asyncio.create_task(adversary(urls, log, wire, tail))
        deadline = time.time() + timeout
        while time.time() < deadline:
            tail.poll()
            if tail.count("producer", "finished") and attack.done():
                break
            if attack.done() and attack.exception():
                raise attack.exception()
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)
        tail.poll()

        report.producer = (await call(urls["producer"], "get_report")).data
        report.ledger = (await call(urls["consumer"], "get_ledger")).data["entries"]
        report.consumer_stats = (await call(urls["consumer"], "get_stats")).data
        report.events = tail.events
        check_invariants(report)
        if not quiet:
            print_summary(report)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
    return report


def check_invariants(r: Report) -> None:
    def check(text: str, passed: bool) -> None:
        r.checks.append((text, bool(passed)))

    results = r.producer.get("results", {})
    settled = sorted(t for t, s in results.items() if s == "settled")
    ledger_ids = [e["payload"]["txn_id"] for e in r.ledger]
    consumer_events = [e for e in r.events if e["actor"] == "consumer"]

    check("every ledger record belongs to the verified business BIZ-100245",
          r.ledger and all(e["business_id"] == "BIZ-100245" for e in r.ledger))
    check("ledger holds exactly the invoices the producer saw settled (no more, no fewer)",
          sorted(ledger_ids) == settled)
    check("no transaction was ingested twice (replay blocked)", len(ledger_ids) == len(set(ledger_ids)))
    check("no forged or tampered record reached the ledger",
          not any(i.startswith("FAKE") for i in ledger_ids)
          and all(e["payload"]["amount"] < 100_000 for e in r.ledger))
    check("the unvetted business could not onboard",
          any(e["kind"] == "onboard_denied" for e in r.events))
    check("forged credentials were rejected at the issuer check",
          sum(1 for e in consumer_events if e["kind"] == "rejected" and e.get("check") == "issuer") >= 3)
    check("the repeat offender's key was quarantined autonomously",
          any(e["kind"] == "quarantine" for e in consumer_events))
    check("tampering was rejected at the signature check",
          any(e["kind"] == "rejected" and e.get("check") == "signature" for e in consumer_events))
    check("replay was rejected at the replay check",
          any(e["kind"] == "rejected" and e.get("check") == "replay" for e in consumer_events))
    check("the producer renewed its expired credential on its own and carried on",
          r.producer.get("renewals", 0) >= 1 and any(e["kind"] == "input_required" for e in consumer_events))
    check("the out-of-scope credit inquiry was refused without stopping the producer",
          results.get("INV-004", "").startswith("rejected:scope"))
    check("after the registry revoked it, the producer halted and held the rest",
          r.producer.get("halted") and any(s == "held" for s in results.values()))
    quarantine_t = next((e["t"] for e in consumer_events if e["kind"] == "quarantine"), None)
    check("the real producer kept settling after the impostor was quarantined (no collateral damage)",
          quarantine_t is not None and any(
              e["actor"] == "producer" and e["kind"] == "settled" and e["t"] > quarantine_t for e in r.events))


def print_summary(r: Report) -> None:
    print(f"\n{BOLD}Producer's final view{RESET}")
    for txn, status in sorted(r.producer.get("results", {}).items()):
        color = GREEN if status == "settled" else (YELLOW if status == "held" else RED)
        print(f"  {txn}  {color}{status}{RESET}")
    print(f"\n{BOLD}Consumer ledger{RESET} {DIM}(written through the MCP ledger tool){RESET}")
    for e in r.ledger:
        p = e["payload"]
        print(f"  {p['txn_id']}  {p['amount']:>10,.2f} {p['currency']}  from {e['business_id']} ({e['legal_name']})")
    print(f"\n{BOLD}Invariants{RESET}")
    for text, passed in r.checks:
        print(f"  {GREEN + 'PASS' if passed else RED + 'FAIL'}{RESET}  {text}")
    print(f"\n{GREEN + 'All invariants held.' if r.ok else RED + 'INVARIANT FAILURE.'}{RESET}")


def main() -> None:
    report = asyncio.run(run_scenario())
    sys.exit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
