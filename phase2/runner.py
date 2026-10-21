"""
Autonomous scenario runner.

Starts the Trusted Agent Registry, the Bureau Core, a trusted source agent and a
rogue source agent as four separate A2A services (each source agent and the
Bureau Core also start their own MCP tool server), then steps back. Nobody
scripts the agents' behaviour. The runner only

  - injects one external risk signal (a watchlist hit on the trusted agent)
  - narrates the shared event log as it happens
  - checks, at the end, that the invariants of the system held

Run:  python -m phase2.runner          (or: make phase2-demo)
Exit code is non-zero if any invariant fails.
"""

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .a2a_util import call, log_event, wait_ready

GREEN, RED, YELLOW, CYAN, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m")
ACTOR_COLOR = {"registry": CYAN, "bureau": YELLOW, "trusted": GREEN, "rogue": RED, "runner": DIM}
TRUSTED_ID = "SOS-CA-CONNECTOR"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Report:
    events: list = field(default_factory=list)
    store: list = field(default_factory=list)
    audit: list = field(default_factory=list)
    trusted: dict = field(default_factory=dict)
    rogue: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)  # (description, passed)

    @property
    def ok(self) -> bool:
        return all(passed for _, passed in self.checks)


# --- narration -------------------------------------------------------------

def describe(e: dict) -> str:
    g = e.get
    return {
        "registered": lambda: f"registered {g('agent_id')} for jurisdictions {g('jurisdictions')}",
        "register_denied": lambda: f"REFUSED to register {g('agent_id')}: {g('reason')}",
        "revoked": lambda: f"REVOKED {g('agent_id')} (source={g('source')}, severity={g('severity')}) automatically",
        "flagged": lambda: f"flagged {g('agent_id')} (severity={g('severity')})",
        "submit": lambda: f"submitting '{g('legal_name')}' ({g('state_filing_id')}) as {g('agent_id')}",
        "ingested": lambda: f"{g('state_filing_id')} accepted by the bureau ({g('status')})",
        "refused": lambda: f"{g('state_filing_id')} refused: {g('status')}",
        "halt": lambda: f"HALT: {g('reason')}",
        "retry": lambda: f"retrying {g('state_filing_id')}: {g('reason')}",
        "blocked": lambda: f"{g('state_filing_id')} blocked: {g('status')}",
        "slipped_through": lambda: f"{g('state_filing_id')} SLIPPED THROUGH",
        "adapt": lambda: f"ADAPT: {g('reason')}",
        "give_up": lambda: f"GIVE UP: {g('reason')}",
        "crashed": lambda: f"CRASHED: {g('reason')}",
        "finished": lambda: "finished its run",
        "decided": lambda: (f"{g('status')}  '{g('legal_name')}' from {g('agent_id')}  [{g('check')}] {g('reason')}"),
        "quarantined": lambda: f"QUARANTINED {g('agent_id')} after {g('strikes')} unauthorized submissions",
        "failed_closed": lambda: f"{g('reason')}; failing closed",
        "note": lambda: g("note"),
    }.get(e["kind"], lambda: json.dumps({a: b for a, b in e.items() if a not in ("t", "actor", "kind")}))()


def render(e: dict, t0: float) -> str:
    color = ACTOR_COLOR.get(e["actor"], "")
    line = describe(e)
    if e["kind"] == "decided":
        line = (GREEN if e.get("status") == "SUCCESS" else RED) + line.split("  ", 1)[0] + RESET + "  " + line.split("  ", 1)[1]
    return f"{DIM}+{e['t'] - t0:5.1f}s{RESET} {color}{e['actor']:<9}{RESET} {line}"


class Tail:
    """Reads new lines from the shared event log and prints them in order."""

    def __init__(self, path: Path, t0: float, quiet: bool):
        self.path, self.t0, self.quiet, self.pos, self.events = path, t0, quiet, 0, []

    def poll(self) -> None:
        if not self.path.exists():
            return
        with self.path.open() as f:
            f.seek(self.pos)
            chunk = f.read()
            self.pos = f.tell()
        for line in chunk.splitlines():
            e = json.loads(line)
            self.events.append(e)
            if not self.quiet:
                print(render(e, self.t0))

    def count(self, actor: str, kind: str) -> int:
        return sum(1 for e in self.events if e["actor"] == actor and e["kind"] == kind)


# --- orchestration -----------------------------------------------------------

async def run_scenario(quiet: bool = False, interval: float = 0.7, timeout: float = 60.0) -> Report:
    run_dir = Path(tempfile.mkdtemp(prefix="trust-run-"))
    log = run_dir / "events.jsonl"
    ports = {n: free_port() for n in ("registry", "bureau", "trusted", "rogue")}
    urls = {n: f"http://127.0.0.1:{p}" for n, p in ports.items()}
    t0 = time.time()
    tail = Tail(log, t0, quiet)
    procs: list[subprocess.Popen] = []
    admin = secrets.token_hex(16)  # operator credential for this run only

    def spawn(name: str, module: str, *args: str) -> None:
        out = open(run_dir / f"{name}.out", "w")
        procs.append(subprocess.Popen([sys.executable, "-m", module, *args, "--log", str(log)],
                                      stdout=out, stderr=subprocess.STDOUT,
                                      env={**os.environ, "TRUST_ADMIN_TOKEN": admin}))

    report = Report()
    try:
        if not quiet:
            print(f"{BOLD}Phase 2: autonomous agent trust over A2A + MCP{RESET}  {DIM}(run dir {run_dir}){RESET}\n")
        spawn("registry", "phase2.registry_service", "--port", str(ports["registry"]))
        await wait_ready(urls["registry"])
        spawn("bureau", "phase2.bureau_core", "--port", str(ports["bureau"]),
              "--registry-url", urls["registry"], "--store-dir", str(run_dir))
        await wait_ready(urls["bureau"])
        log_event(str(log), "runner", "note", note="registry and bureau are up; starting both source agents, then stepping back")
        for mode in ("trusted", "rogue"):
            spawn(mode, "phase2.source_agent", "--mode", mode, "--port", str(ports[mode]),
                  "--registry-url", urls["registry"], "--bureau-url", urls["bureau"], "--interval", str(interval))

        deadline, signalled = time.time() + timeout, False
        while time.time() < deadline:
            tail.poll()
            if not signalled and tail.count("trusted", "ingested") >= 4:
                signalled = True
                log_event(str(log), "runner", "note", note=f"external risk signal: watchlist hit on {TRUSTED_ID} sent to the registry")
                await call(urls["registry"], "watchlist_event",
                           {"agent_id": TRUSTED_ID, "source": "sanctions-watchlist", "severity": "high",
                            "admin_token": admin})
            if tail.count("trusted", "finished") and tail.count("rogue", "finished"):
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)
        tail.poll()

        report.trusted = (await call(urls["trusted"], "get_report")).data
        report.rogue = (await call(urls["rogue"], "get_report")).data
        report.store = (await call(urls["bureau"], "get_store", {"admin_token": admin})).data["filings"]
        report.audit = (await call(urls["bureau"], "get_audit", {"admin_token": admin})).data["entries"]
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
    def check(text: str, passed) -> None:
        r.checks.append((text, bool(passed)))

    ev = lambda actor, kind: [e for e in r.events if e["actor"] == actor and e["kind"] == kind]  # noqa: E731
    stored_ids = sorted(f["state_filing_id"] for f in r.store)
    ingested = sorted(i for i, s in r.trusted["results"].items() if s == "ingested")
    decisions = ev("bureau", "decided")
    submissions = ev("trusted", "submit") + ev("rogue", "submit")
    quarantine = next(iter(ev("bureau", "quarantined")), None)

    check("every filing in the verified store came from the trusted agent",
          r.store and all(f["submitted_by"] == TRUSTED_ID for f in r.store))
    check("nothing the rogue agent submitted reached the verified store",
          not any(i.startswith("C99999") for i in stored_ids))
    check("the store holds exactly what the trusted agent saw accepted", stored_ids == ingested)
    check("the rogue's own identity was rejected as unauthorized, then quarantined without an operator",
          any(d["status"] == "UNAUTHORIZED_SOURCE_REJECTED" and d["agent_id"] == "ROGUE-AGENT-7" for d in decisions)
          and quarantine is not None)
    check("the rogue then tried to impersonate the trusted agent and failed the signature challenge",
          any(e["kind"] == "adapt" for e in r.events if e["actor"] == "rogue")
          and any(d["check"] == "signature" for d in decisions))
    check("the rogue gave up once every route was blocked", ev("rogue", "give_up"))
    check("a trusted-agent filing outside its jurisdiction was refused, and so was an invalid one",
          r.trusted["results"].get("D1000234") == "rejected:OUT_OF_JURISDICTION_REJECTED"
          and r.trusted["results"].get("C4829303") == "rejected:INVALID_PAYLOAD_REJECTED")
    check("the registry revoked the trusted agent on its own when the risk signal arrived", ev("registry", "revoked"))
    check("the trusted agent halted by itself and held every remaining filing",
          r.trusted["halted"] and any(s == "held" for s in r.trusted["results"].values()))
    check("quarantining the rogue did not stop the trusted agent",
          quarantine and any(e["t"] > quarantine["t"] for e in ev("trusted", "ingested")))
    check("every submission was audited, with a reason", len(r.audit) == len(submissions)
          and all(e["status"] and e["reason"] for e in r.audit))


def print_summary(r: Report) -> None:
    print(f"\n{BOLD}Verified store{RESET} {DIM}(written through the MCP store tool){RESET}")
    for f in r.store:
        print(f"  {f['state_filing_id']}  {f['legal_name']} ({f['jurisdiction']}, formed {f['formation_date']})  via {f['submitted_by']}")
    print(f"\n{BOLD}Audit log{RESET}")
    counts: dict[str, int] = {}
    for e in r.audit:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {(GREEN if status == 'SUCCESS' else RED)}{status:<30}{RESET} {n}")
    print(f"\n{BOLD}Invariants{RESET}")
    for text, passed in r.checks:
        print(f"  {GREEN + 'PASS' if passed else RED + 'FAIL'}{RESET}  {text}")
    print(f"\n{GREEN + 'All invariants held.' if r.ok else RED + 'INVARIANT FAILURE.'}{RESET}")


def main() -> None:
    sys.exit(0 if asyncio.run(run_scenario()).ok else 1)


if __name__ == "__main__":
    main()
