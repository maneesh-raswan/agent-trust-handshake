# Evolution: from handshake to autonomous economy

## Phase 1: prove the trust model (`git checkout phase-1`)

Everything in one process. A Registry object issues a credential, a Producer object signs an envelope, a Consumer object runs the checks. A scripted demo walks six scenarios. The point was to isolate the *trust logic* (identity, signature, freshness, replay, scope, live status) from any plumbing, and to prove each failure mode is stopped by its own check.

What it could not show: agents that are independent. Everything shared memory and a script told each one what to do.

## Phase 2: put it between autonomous agents

| Concern | Phase 1 | Phase 2 |
|---|---|---|
| Process boundary | one process | five: three A2A agents, two MCP tool servers |
| Discovery | objects passed by reference | A2A Agent Cards (`/.well-known/agent-card.json`) |
| Registry status | a Python callable | an A2A call on every transaction, fail closed |
| Consumer receipt key | passed in code | published in the Consumer's Agent Card as an extension, read by the Producer |
| Decision signalling | a `Decision` object | A2A task state: `completed`, `rejected`, `input-required`, `failed` |
| Producer behaviour | one call per scenario | an autonomous loop with renew, skip and halt behaviours |
| Consumer behaviour | accept or reject | plus quarantine policy and recoverable `input-required` |
| Registry behaviour | issue and revoke on request | plus KYB gate, scope capping, autonomous revocation on risk signals |
| Data access | in-memory | tools over MCP (ERP for the producer, ledger for the consumer) |
| Evidence | printed checks | a shared event log and end-of-run invariants (exit code) |

### The trust core did not change in spirit

`trust/` is shared by both phases. Three small, backward compatible additions made phase 2 possible, and phase 1's tests pass unchanged:

1. `TrustRegistry.register_business(..., ttl_seconds=...)` so credentials can be short lived
2. `TrustRegistry.record(business_id)` so the registry service can renew for the registered key
3. `ConsumerAgent(..., ledger_sink=...)` so accepted records can go to an MCP tool instead of a file

### Things phase 2 taught us

- **Numbers on the wire.** A2A data parts are protobuf values, where every number is a double. `1792440103` returns as `1792440103.0` and `12500.0` as `12500`, which silently breaks signatures over canonical JSON. Payloads travel as JSON strings inside the data part. A test pins this.
- **A race between expiry and revocation.** With short-lived credentials, a revoked producer's next send can fail on *expiry* before *status*, so it tries to renew, and the Registry correctly refuses. The first version crashed there. Now a refused renewal is treated as a halt signal. Found by running the scenario repeatedly.
- **Quarantine can be weaponized.** Counting every rejection against the sender lets an attacker replay or tamper with a victim's traffic and get the victim quarantined. Only forged credentials, which are attributable to the presenting key, count as strikes.

### Why A2A and MCP together

MCP is how an agent reaches *its own* tools (the producer's ERP, the consumer's ledger). A2A is how agents reach *each other*. With MCP alone the Consumer would be a tool waiting to be called, not an agent that can refuse, ask for more, or quarantine a sender.

## Viewing the history

```bash
git tag                       # phase-1, phase-2
git diff phase-1 phase-2 --stat
git checkout phase-1          # the original, runnable with `make phase1-demo`
```
