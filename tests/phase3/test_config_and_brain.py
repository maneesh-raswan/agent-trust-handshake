import subprocess
import types
from pathlib import Path

import pytest

from phase3 import brain as brain_module
from phase3 import config

ROOT = Path(__file__).resolve().parent.parent.parent


def test_env_file_is_loaded_but_real_environment_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nANTHROPIC_API_KEY='from-file'\nPHASE3_MODEL=m-file\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("PHASE3_MODEL", "m-env")
    config.load_local_config(env)
    assert config.os.environ["ANTHROPIC_API_KEY"] == "from-file"
    assert config.os.environ["PHASE3_MODEL"] == "m-env"


WIF = {"ANTHROPIC_FEDERATION_RULE_ID": "r", "ANTHROPIC_ORGANIZATION_ID": "o",
       "ANTHROPIC_SERVICE_ACCOUNT_ID": "s", "ANTHROPIC_IDENTITY_TOKEN_FILE": "/tmp/jwt"}
CRED_VARS = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", *WIF]


@pytest.fixture
def bare(monkeypatch, tmp_path):
    """No credentials of any kind, and no profile in a fake home."""
    for var in CRED_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "load_local_config", lambda *a: None)
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    return tmp_path


def test_missing_credentials_fail_with_instructions_not_a_traceback(bare):
    assert not config.has_credentials()
    with pytest.raises(SystemExit) as exc:
        config.require_credentials()
    msg = str(exc.value)
    assert ".env.example" in msg and "ant auth login" in msg and "federation" in msg and "phase3-demo-stub" in msg


@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"])
def test_a_key_or_a_token_is_enough(bare, monkeypatch, var):
    monkeypatch.setenv(var, "x")
    assert config.has_credentials()


def test_workload_identity_federation_is_enough_when_fully_configured(bare, monkeypatch):
    for k, v in WIF.items():
        monkeypatch.setenv(k, v)
    assert config.has_credentials()
    monkeypatch.delenv("ANTHROPIC_SERVICE_ACCOUNT_ID")  # a half-configured federation is not
    assert not config.has_credentials()


def test_a_login_profile_is_enough(bare):
    profile = bare / ".config" / "anthropic"
    profile.mkdir(parents=True)
    assert not config.has_credentials()  # an empty directory is not a profile
    (profile / "default.json").write_text("{}")
    assert config.has_credentials()


@pytest.fixture
def clean_models(monkeypatch):
    for var in ("PHASE3_MODEL", "PHASE3_PRESET", *(f"PHASE3_MODEL_{r.upper()}" for r in config.ROLES)):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "load_local_config", lambda *a: None)


def test_default_is_the_cheapest_model(clean_models):
    assert config.models_in_use() == {r: "claude-haiku-5-5" for r in config.ROLES}


def test_presets(clean_models, monkeypatch):
    for preset, expected in config.PRESETS.items():
        monkeypatch.setenv("PHASE3_PRESET", preset)
        assert config.model("trusted") == expected


def test_most_specific_setting_wins(clean_models, monkeypatch):
    monkeypatch.setenv("PHASE3_PRESET", "best")
    monkeypatch.setenv("PHASE3_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("PHASE3_MODEL_ROGUE", "some-other-model")
    assert config.model("trusted") == "claude-sonnet-5-5"   # PHASE3_MODEL beats the preset
    assert config.model("rogue") == "some-other-model"      # a role beats everything
    assert config.model() == "claude-sonnet-5-5"


def test_a_typo_in_the_preset_fails_clearly(clean_models, monkeypatch):
    monkeypatch.setenv("PHASE3_PRESET", "cheapest")
    with pytest.raises(SystemExit, match="cheap"):
        config.model("trusted")


def test_the_key_can_never_be_committed():
    ignored = subprocess.run(["git", "check-ignore", ".env", ".env.local"], cwd=ROOT, capture_output=True, text=True)
    assert ignored.stdout.split() == [".env", ".env.local"]
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert ".env" not in tracked


def test_no_real_looking_api_key_in_any_file_that_could_be_committed():
    """Placeholders like `sk-ant-...` are fine; anything shaped like a real key is not."""
    import re
    shaped_like_a_key = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")
    # Tracked files plus untracked-but-not-ignored ones: everything a `git add -A` would pick up.
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                           capture_output=True, text=True).stdout.split()
    for name in files:
        path = ROOT / name
        if path.suffix in {".png"} or not path.exists():
            continue
        assert not shaped_like_a_key.search(path.read_text(errors="ignore")), name


class FakeBlock(types.SimpleNamespace):
    pass


def fake_response(stop_reason, blocks):
    return types.SimpleNamespace(stop_reason=stop_reason, content=blocks)


def test_response_is_translated_and_thinking_blocks_are_kept_for_replay():
    thinking = FakeBlock(type="thinking", thinking="")
    text = FakeBlock(type="text", text="I'll file it.")
    call = FakeBlock(type="tool_use", id="toolu_1", name="submit_filing", input={"state_filing_id": "C1"})
    turn = brain_module.AnthropicBrain._to_turn(fake_response("tool_use", [thinking, text, call]))
    assert turn.stop == "tool_use" and turn.text == "I'll file it."
    assert turn.tool_calls == [{"id": "toolu_1", "name": "submit_filing", "input": {"state_filing_id": "C1"}}]
    assert turn.assistant_content[0] is thinking  # replayed exactly as returned


def test_refusal_and_truncation_are_surfaced():
    assert brain_module.AnthropicBrain._to_turn(fake_response("refusal", [])).stop == "refusal"
    assert brain_module.AnthropicBrain._to_turn(fake_response("max_tokens", [])).stop == "max_tokens"


def fake_client(sent):
    async def plain(**kw):
        sent["plain"] = kw
        return fake_response("end_turn", [FakeBlock(type="text", text="ok")])

    async def beta(**kw):
        sent["beta"] = kw
        return fake_response("end_turn", [FakeBlock(type="text", text="ok")])

    return types.SimpleNamespace(messages=types.SimpleNamespace(create=plain),
                                 beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=beta)))


TOOLS = [{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
HI = [{"role": "user", "content": "hi"}]


@pytest.fixture
def api(monkeypatch, clean_models):
    import anthropic
    sent = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("PHASE3_EFFORT", raising=False)
    monkeypatch.delenv("PHASE3_FALLBACKS", raising=False)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda: fake_client(sent))
    return sent


async def test_default_request_is_plain_cheap_and_forces_nothing(api, monkeypatch):
    monkeypatch.setenv("PHASE3_EFFORT", "low")
    await brain_module.AnthropicBrain("trusted", fallbacks=True).decide("sys", HI, TOOLS)
    kw = api["plain"]  # Haiku has no refusal fallback, so no beta call
    assert "beta" not in api and kw["model"] == "claude-haiku-5-5" and kw["output_config"] == {"effort": "low"}
    assert kw["tools"] == TOOLS and "tool_choice" not in kw and "thinking" not in kw and "fallbacks" not in kw


async def test_fallback_is_used_only_where_the_model_supports_it_and_never_for_the_red_team(api, monkeypatch):
    monkeypatch.setenv("PHASE3_MODEL", "claude-sonnet-5-5")
    await brain_module.AnthropicBrain("trusted", fallbacks=True).decide("sys", HI, TOOLS)
    assert api["beta"]["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in api["beta"]["betas"]
    api.clear()
    await brain_module.AnthropicBrain("rogue", fallbacks=False).decide("sys", HI, TOOLS)
    assert "beta" not in api and "fallbacks" not in api["plain"]  # the red team is not rescued by another model
    api.clear()
    monkeypatch.setenv("PHASE3_FALLBACKS", "0")
    await brain_module.AnthropicBrain("trusted", fallbacks=True).decide("sys", HI, TOOLS)
    assert "beta" not in api


async def test_each_role_can_use_its_own_model(api, monkeypatch):
    monkeypatch.setenv("PHASE3_MODEL_ROGUE", "claude-opus-5-5")
    await brain_module.AnthropicBrain("rogue", fallbacks=False).decide("sys", HI, TOOLS)
    assert api["plain"]["model"] == "claude-opus-5-5"
    await brain_module.AnthropicBrain("advisor", fallbacks=False).decide("sys", HI, [])
    assert api["plain"]["model"] == "claude-haiku-5-5" and "tools" not in api["plain"]
