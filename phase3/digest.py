"""
The only thing the advisory LLM ever sees of the audit log.

The audit log records what senders wrote, including senders the bureau rejected. That text is
attacker-controlled, so the digest is built to leave it out: a sender the bureau could not
authenticate contributes only the bureau's own words (status, check, reason), never its
legal_name, filing id or agent_id. Sources are relabelled source-1, source-2, ... so even an
agent_id can't smuggle text in.

For senders that did authenticate (their signature verified), the filing fields are included, quoted,
as untrusted data. "Authenticate before you read", applied to the model as well as to the code.
"""

# Checks that run only after the sender's signature has verified.
AUTHENTICATED_CHECKS = {"all", "payload", "jurisdiction", "duplicate", "freshness", "replay"}
MAX_ENTRIES = 150


def build_digest(entries: list[dict]) -> dict:
    labels: dict[str, str] = {}
    counts: dict[str, int] = {}
    sources: dict[str, dict[str, int]] = {}
    out = []
    for seq, e in enumerate(entries[:MAX_ENTRIES], 1):
        label = labels.setdefault(e.get("agent_id") or "", f"source-{len(labels) + 1}")
        authenticated = e.get("check") in AUTHENTICATED_CHECKS
        item = {"seq": seq, "source": label, "status": e["status"], "check": e.get("check", ""),
                "reason": e.get("reason", ""), "sender_authenticated": authenticated}
        if authenticated:
            item["legal_name"] = e.get("legal_name")
            item["state_filing_id"] = e.get("state_filing_id")
        out.append(item)
        counts[e["status"]] = counts.get(e["status"], 0) + 1
        per = sources.setdefault(label, {})
        per[e["status"]] = per.get(e["status"], 0) + 1
    return {"counts": counts, "sources": sources, "entries": out}
