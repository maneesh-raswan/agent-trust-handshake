# Project Blueprint: Trust-Verified Bureau Data Ingestion

> This is the founding vision and spec the project is built from. It is kept here as written.
> See [Traceability](#7-traceability-blueprint-to-code) at the bottom for where each part lives in the code.

## 1. Context: What a Commercial Credit Bureau Does

Commercial credit bureaus (such as Dun & Bradstreet, Experian Business, and Equifax Business) aggregate data from suppliers, public records, and financial institutions to evaluate creditworthiness, assess financial risk, and predict business reliability. They compile payment histories, public records, and financial accounts into a unified profile that lenders, vendors, insurance companies, and government contractors use to determine whether, and under what terms, to do business with a company.

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
