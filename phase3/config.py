"""
Local configuration for phase 3.

Phase 3's live run calls a real model, so it needs credentials from somewhere. The Anthropic SDK
finds them itself, in this order: ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, a login profile from
`ant auth login`, then workload identity federation. Nothing here needs to be an API key.

Wherever they come from they are yours and stay local: a `.env` file is git-ignored and only
`.env.example` with placeholders is in the repo. With no credentials at all, use the stubbed run.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Which model each preset uses. "cheap" is the default: a demo that makes ~100 calls should cost cents.
PRESETS = {
    "cheap": "claude-haiku-5-5",      # $0.10 / $0.50 per million tokens: the cheapest current model
    "balanced": "claude-sonnet-5-5",  # $2 / $10
    "best": "claude-opus-5-5",        # $4 / $20
}
DEFAULT_PRESET = "cheap"
ROLES = ("trusted", "rogue", "advisor")
# Server-side refusal fallback exists only for these models; asking for it on any other model is an error.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


def load_local_config(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from .env into the environment. Real environment variables win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def has_credentials() -> bool:
    """Is there any credential source the SDK could resolve? (It validates them on first use.)"""
    load_local_config()
    env = os.environ
    federation = all(env.get(k) for k in ("ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID",
                                          "ANTHROPIC_SERVICE_ACCOUNT_ID")) and (
        env.get("ANTHROPIC_IDENTITY_TOKEN_FILE") or env.get("ANTHROPIC_IDENTITY_TOKEN"))
    profile_dir = Path.home() / ".config" / "anthropic"
    profile = profile_dir.is_dir() and any(profile_dir.iterdir())
    return bool(env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN") or federation or profile)


def model(role: str | None = None) -> str:
    """The model for a role. Most specific wins:

        PHASE3_MODEL_TRUSTED / _ROGUE / _ADVISOR   one role
        PHASE3_MODEL                               every role
        PHASE3_PRESET = cheap | balanced | best    (default: cheap)
    """
    load_local_config()
    env = os.environ
    if role and env.get(f"PHASE3_MODEL_{role.upper()}"):
        return env[f"PHASE3_MODEL_{role.upper()}"]
    if env.get("PHASE3_MODEL"):
        return env["PHASE3_MODEL"]
    preset = env.get("PHASE3_PRESET", DEFAULT_PRESET)
    if preset not in PRESETS:
        raise SystemExit(f"PHASE3_PRESET must be one of {sorted(PRESETS)}, not '{preset}'")
    return PRESETS[preset]


def models_in_use() -> dict[str, str]:
    return {role: model(role) for role in ROLES}


def require_credentials() -> None:
    if not has_credentials():
        raise SystemExit(
            "Phase 3's live run needs credentials for a model. Any one of these works:\n"
            "  - an API key:       cp .env.example .env, then put your key in .env (git-ignored)\n"
            "  - a login profile:  ant auth login\n"
            "  - workload identity federation (ANTHROPIC_FEDERATION_RULE_ID and friends)\n"
            "No credentials? Run the free stubbed version instead: make phase3-demo-stub")
