# Evolution

The project started from [the blueprint](BLUEPRINT.md): trusted ingestion of Secretary of State filings into a credit bureau, stopping at the Bureau Core. This page records how the code got from there to where it is.

## Step 1: a trust-model sketch

The first sketch ([archive/handshake-flow-v0.png](archive/handshake-flow-v0.png)) explored the trust ideas with a trade-payment example and registry-signed credentials: producer, consumer, registry, signed receipts. It proved the four questions every consumer must answer: is the identity real, is the sender that identity, is the message fresh, is it allowed. Git tags `phase-1` and `phase-2` mark the two stages built on that sketch.

## Step 2: align to the blueprint

The blueprint is narrower and simpler, so the code was cut back to it rather than extended. What changed:

| Before | Now (blueprint) |
|---|---|
| Trade payment records | Secretary of State business formation filings |
| Producer / consumer | Source agents (trusted and rogue) / Bureau Core |
| Registry-signed credentials, expiry, renewal, receipts | A trusted agent registry the bureau consults live; `auth_token` signs each filing |
| One checker | Three named modules: Agent Authentication, Ingestion Pipeline, Audit Logger |
| Pass/fail with a reason | Audit statuses, including the blueprint's `SUCCESS` and `UNAUTHORIZED_SOURCE_REJECTED` |
| Adversary scripted by the runner | The rogue is itself an autonomous agent |
| ERP and ledger tools | A source's SoS feed, and the bureau's store and audit log |

Credentials, renewal, receipts and the network tap were dropped: they added moving parts without adding to the story the blueprint tells.

## The two phases

**Phase 1** runs the whole thing in one process: nine scenarios, one per way a submission can go wrong. It isolates the trust logic from any plumbing.

**Phase 2** puts the identical core on a network. The registry, the Bureau Core, and each source agent are separate A2A services. Each source agent and the Bureau Core use MCP tool servers for their data. Nothing in `trust/` changed to make this work; the Bureau Core gains only a store and an audit sink so the service can persist through MCP.

| Concern | Phase 1 | Phase 2 |
|---|---|---|
| Registry lookup | a function call | an A2A call on every submission, fail closed |
| Result | a `Decision` object | A2A task state plus the audit status |
| Source agents | called by the demo | autonomous services with their own loop |
| Rogue | a scripted call | an agent that adapts, then gives up |
| Bureau policy | reject | plus quarantine of repeat offenders |
| Registry policy | revoke on request | plus autonomous revocation on a risk signal |
| Evidence | printed results | shared event log and end-of-run invariants (exit code) |

## Things phase 2 taught us

- **Numbers on the wire.** A2A data parts are protobuf values, where every number is a double, which would silently break signatures over canonical JSON. Payloads travel as JSON strings inside the data part, and a test pins it.
- **Quarantine can be weaponized.** If a bad signature counted as a strike against the claimed agent_id, anyone could claim a trusted agent's identity and get it blocked. Only an *unknown* agent_id earns strikes.
- **Deterministic signatures collide.** Ed25519 signs deterministically, so two submissions of one filing in the same instant carried the same `auth_token` and tripped replay protection. Timestamps now have microsecond precision.
- **Races between policies.** Short-lived credentials made a revoked agent's next call fail on expiry before status, and the first version crashed trying to renew. Part of why credentials were dropped.

## Viewing the history

```bash
git log --oneline
git tag                          # phase-1 and phase-2: the first sketch
git checkout phase-1             # runnable with `make phase1-demo`
```
