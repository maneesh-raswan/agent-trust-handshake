# Project Blueprint: Trust-Verified Bureau Data Ingestion

> This is the founding vision and spec the project is built from. It is kept here as written.
> See [Traceability](#7-traceability-blueprint-to-code) at the bottom for where each part lives in the code.

## 1. Context: What a Commercial Credit Bureau Does

Commercial credit bureaus (such as Experian Business, Dun & Bradstreet, and Equifax Business) aggregate data from suppliers, public records, and financial institutions to evaluate creditworthiness, assess financial risk, and predict business reliability. They compile payment histories, public records, and financial accounts into a unified profile that lenders, vendors, insurance companies, and government contractors use to determine whether, and under what terms, to do business with a company.

For any credit profile to be valuable, the underlying data received from suppliers and sources must come from trusted, authentic originators.

## 2. Project Scope & Focused Mission

The primary focus of this project is building only the trusted ingestion path, specifically stopping at the Bureau Core (Ingestion & Verification Engine).

Rather than building full credit scoring algorithms or complex profile dashboards, this prototype focuses entirely on the critical first link of the chain: agent-to-agent trust verification. It guarantees that public filings (such as new business formations from a Secretary of State) are ingested only if submitted by authenticated, verified data provider agents.

## 3. Scope Boundary: What We Are Building

### In scope

- **Source Agent Simulation**
  - **Trusted Agent (Agent_trusted):** simulates an authorized Secretary of State API connector with a valid identity token/key.
  - **Bad Actor Agent (Agent_rogue):** simulates an unverified or malicious source trying to inject fake filings.
- **The Bureau Core (Ingestion & Verification Engine)**
  - **Agent Authentication Module:** verifies agent identities and trust status before processing data.
  - **Ingestion Pipeline:** validates incoming Secretary of State business formation payloads.
  - **Audit Logger:** records accepted filings and flags untrusted/rejected attempts.

### Out of scope (future / excluded)

- Credit scoring algorithms or risk modeling.
- Trade payment history aggregation or financial account linking.
- End-user business credit report dashboards for lenders.

## 4. Simple Walkthrough: Secretary of State Ingestion Example

1. **Trusted ingestion**
   1. Agent_trusted submits a new business filing (`Acme Logistics LLC`, state filing ID, incorporation date).
   2. Bureau Core checks Agent_trusted against its trusted agent registry.
   3. Trust is confirmed. The payload is ingested into the verified store.
   4. Audit logged as `SUCCESS`.
2. **Rejected ingestion**
   1. Agent_rogue submits a fake filing (`Shell Corp Inc`).
   2. Bureau Core challenges Agent_rogue's identity token/signature. Verification fails.
   3. The payload is rejected immediately.
   4. Audit logged as `UNAUTHORIZED_SOURCE_REJECTED`.

## 5. Secretary of State Ingestion Schema

To keep the focus on agent-to-agent trust, the business payload is stripped down to basic identity fields, while the wrapper captures the sending agent's identity and signature.

```json
{
  "agent_metadata": {
    "agent_id": "string",       // Unique ID of the submitting agent
    "auth_token": "string",     // Cryptographic token / API key proving agent identity
    "timestamp": "ISO-8601"     // Time of submission to prevent replay attacks
  },
  "business_filing": {
    "legal_name": "string",     // e.g., "Acme Logistics LLC"
    "state_filing_id": "string",// Unique official ID from the state
    "formation_date": "YYYY-MM-DD",
    "jurisdiction": "string"    // e.g., "CA", "DE", "NY"
  }
}
```

## 6. Decisions made when turning the blueprint into code

The blueprint leaves some choices open. These are the ones we made, so the code and the spec can be read together.

| Open point | Decision |
|---|---|
| What is `auth_token`? | An Ed25519 signature, base64-encoded, over the canonical JSON of `{agent_id, timestamp, business_filing}`. It proves the sender holds the private key the registry has on file for `agent_id`, and it binds the token to this exact filing, so changing a byte of the filing invalidates it. |
| "Trusted agent registry" | A lookup of `agent_id` to public key, status (`active` / `revoked`) and the jurisdictions the agent may file for. In phase 2 it is its own A2A agent that the Bureau Core asks live on every submission. |
| Replay protection | Timestamp must be within 5 minutes, and a given `auth_token` is accepted once. |
| Beyond `SUCCESS` and `UNAUTHORIZED_SOURCE_REJECTED` | Audit statuses for the other ways a submission can fail are listed in the README. Each rejection records the check that failed and why. |
| Jurisdiction scope | A connector for one state may only file for that state. A trusted agent filing outside its jurisdictions is rejected. |
| Autonomy (phase 2) | Both source agents are autonomous services. The bad actor is an agent too, not a script: it keeps trying and adapts. The Bureau Core and the registry make their own decisions (reject, quarantine, revoke) with no human in the loop. |

## 7. Traceability: blueprint to code

| Blueprint item | Where it lives |
|---|---|
| Trusted Agent | `trust/source_agent.py` (phase 1), `phase2/source_agent.py --mode trusted`, feed in `phase2/mcp_sos_feed.py` |
| Bad Actor Agent | same files, `--mode rogue` in phase 2 |
| Trusted agent registry | `trust/registry.py`, served as an A2A agent by `phase2/registry_service.py` |
| Agent Authentication Module | `trust/auth.py` |
| Ingestion Pipeline | `trust/ingestion.py` |
| Verified store | in-memory (phase 1), MCP tool server `phase2/mcp_bureau_store.py` (phase 2) |
| Audit Logger | `trust/audit.py` |
| Bureau Core | `trust/bureau.py`, served as an A2A agent by `phase2/bureau_core.py` |
| Ingestion schema | documented and enforced in `trust/ingestion.py`; built by `trust/source_agent.py` |
| Walkthrough 1 and 2 | first two scenarios of `phase1/demo.py`; the trusted and rogue agents in `phase2/runner.py` |

## 8. Phase 3: LLM as the brain

### 8.1 Where phase 2 stops

In phase 2 the A2A and MCP calls are hard-coded, and in phase 3 we add an LLM as the brain.

How the calls work in phase 2:

- **MCP:** the source agent always calls `list_pending_filings`, then `mark_filing`. The bureau always calls `store_filing` and `audit_append`. No agent chooses a tool.
- **A2A:** the source agent always calls `register_agent`, then `submit_filing`. The bureau always calls `lookup_agent`. The URLs come from command-line flags, so no agent discovers another.
- **Decisions:** how a source reacts to a rejection is an `if` on the audit status. The rogue's "adaptation" is a hand-written two-step script (own identity, then impersonate).

A2A and MCP are only the transport and tool plumbing here. These are autonomous services, and strictly they are not yet agents in the LLM sense.

### 8.2 What phase 3 adds

1. The LLM becomes the brain of the agents that need judgment.
2. The trust decision stays out of its reach.

- **Trusted source agent:** the LLM sees its MCP tool list, picks what to call, reads each A2A answer, and decides what to do next: resend, skip, halt, or escalate. It no longer follows a coded `if`.
- **Rogue agent:** an LLM red-teams the bureau with no script, trying to get `Shell Corp Inc` in by whatever means it invents.
- **Bureau Core:** the authentication and ingestion checks stay deterministic code, with no LLM involvement. An optional advisory layer uses an LLM to explain rejections, summarize the audit log, and spot patterns, but it cannot override a decision.
- **Discovery:** the agents find each other from Agent Cards and find tools by listing them over MCP.

### 8.3 What phase 3 must demonstrate

Phase 3 demonstrates the full scope of the claim that **trust is deterministic, not model judgment**. Put a creative, possibly hallucinating or hostile LLM on the source side, and the invariants must still hold: nothing from the rogue reaches the store, and every submission is audited. The ultimate proof is that they hold with an LLM trying to break them.

**Prompt injection.** A filing's `legal_name` is attacker-controlled text. Authenticating before reading means the bureau never exposes that text to a model from an unproven sender.

### 8.4 Constraints

- **API key:** kept in a local configuration and never committed to the GitHub repo. Anyone testing phase 3 uses their own API key.
- **Testing:** the invariants, not exact output, are the test. CI uses a recorded or stubbed model, so it stays free and stable.
- **Offline phases:** phases 1 and 2 stay runnable offline. Phase 3 needs access to an LLM.

### 8.5 Traceability: phase 3 to code

| Phase 3 item | Where it lives |
|---|---|
| LLM as the brain of the trusted source agent | `phase3/llm_source_agent.py --role trusted`, instructions in `phase3/prompts.py` |
| LLM red-teaming rogue agent | `phase3/llm_source_agent.py --role rogue` |
| Tools listed over MCP, not hard-coded | `phase3/tools.py` (`SourceKit.build_tools` lists the feed server's tools) |
| Discovery from Agent Cards | `phase3/tools.py` (`discover_agent`, `call_agent`) |
| Bureau Core stays deterministic | `trust/` and `phase2/bureau_core.py`, unchanged |
| Optional advisory layer | `phase3/advisor_service.py` |
| Prompt injection: no text from unproven senders reaches a model | `phase3/digest.py`; checked by the runner and `tests/phase3/test_digest.py` |
| Invariants hold whatever the brains do | `phase3/runner.py` (`check_invariants`), shown to fail in `tests/phase3/test_invariants.py` |
| Credentials local, never committed (an API key, or any other credential source the SDK supports) | `phase3/config.py`, `.env.example`, `.gitignore`, `tests/phase3/test_config_and_brain.py` |
| Recorded or stubbed model for CI | `phase3/stub_brain.py`, `make phase3-demo-stub`, the `phase3` CI job |
| Phases 1 and 2 stay offline | `anthropic` is imported only by the live brain; the `phase1` and `phase2` CI jobs install no phase 3 extras |

Skills that operate the bureau or revoke an agent (`watchlist_event`, `get_store`, `get_audit`) are operator-only: they need an admin token and are not advertised on the Agent Cards. A discovering red team would otherwise find them.
