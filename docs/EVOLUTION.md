# Evolution

The project started from [the spec](SPEC.md): trusted ingestion of Secretary of State filings into a credit bureau, stopping at the Bureau Core. This page records how the code got from there to where it is.

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

## Phase 3: a brain for the agents, not for the trust decision

Phase 2's agents were services running fixed code: every A2A and MCP call was hard-coded, and "adaptation" was a script. Phase 3 ([spec](SPEC.md#8-phase-3-llm-as-the-brain)) lets an LLM decide what the source agents do, from the tools they list over MCP and the agents they discover through Agent Cards, while the Bureau Core stays deterministic. The point is the proof: with a creative, possibly hallucinating or hostile model on the source side, the invariants still hold.

| Concern | Phase 2 | Phase 3 |
|---|---|---|
| What to call | hard-coded sequence | the model picks from discovered tools and skills |
| Reacting to an answer | an `if` on the audit status | the model reads it and decides: resend, skip, halt, escalate |
| The rogue | a two-step script | a red-team model with no script |
| Finding other agents | URLs from flags | Agent Cards |
| Bureau Core | deterministic | **unchanged, deterministic** |
| New | | an advisory LLM that explains and summarizes, with no authority |
| Evidence | invariants on the run | invariants that hold *whatever the brains do*, shown to fail when violated |

What phase 3 changed in earlier code, because an LLM that discovers things exercises the whole surface:

- **Operator-only skills.** A discovering red team finds every skill on an Agent Card, so `watchlist_event` (revoke any agent), `get_store` and `get_audit` could be called by anyone. They now need an admin token and are not advertised. Phase 2 had listed this as a known limitation; an LLM made it real.
- **`feeds.py`.** Importing the MCP server module to read the filings configured logging in the runner process; the data moved to a plain module.

Lessons:

- **Authenticate before the model reads.** The audit log records attacker-controlled text even for rejected submissions, so it cannot go to the advisory model as is. The digest drops it, relabels sources, and a test proves nothing leaks.
- **The invariants have to be able to fail.** A check that can never go red proves nothing, so the tests feed each invariant a violation.
- **A hallucinated tool is an expected input.** The loop turns a nonexistent tool, bad arguments or a raising handler into an error the model can read, and caps the run.
- **Stubs are a fixture, not a substitute.** The scripted brains hallucinate and attack on purpose, which makes CI a real test of the claim. They say nothing about how a real model behaves; that is what the live run is for.

## Things phase 2 taught us

- **Numbers on the wire.** A2A data parts are protobuf values, where every number is a double, which would silently break signatures over canonical JSON. Payloads travel as JSON strings inside the data part, and a test pins it.
- **Quarantine can be weaponized.** If a bad signature counted as a strike against the claimed agent_id, anyone could claim a trusted agent's identity and get it blocked. Only an *unknown* agent_id earns strikes.
- **Deterministic signatures collide.** Ed25519 signs deterministically, so two submissions of one filing in the same instant carried the same `auth_token` and tripped replay protection. Timestamps now have microsecond precision.
- **Races between policies.** Short-lived credentials made a revoked agent's next call fail on expiry before status, and the first version crashed trying to renew. Part of why credentials were dropped.

## Viewing the history

The `phase-1` and `phase-2` tags are the first sketch (see Step 1 above), before the code was cut back to the blueprint.

```bash
git log --oneline
git tag                          # phase-1 and phase-2: the first sketch
git checkout phase-1             # runnable with `make phase1-demo`
```
