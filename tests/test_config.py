import pytest

from transparent_agent.config import Config, ConfigError
from transparent_agent.prompt import build_system_prompt


def test_config_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    config = Config.from_env()
    assert config.workspace == tmp_path.resolve()
    assert config.model == "gpt-4o"
    assert config.max_steps == 40
    assert config.bash_timeout == 120.0


def test_config_reads_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-chat")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MAX_STEPS", "7")
    monkeypatch.setenv("BASH_TIMEOUT_S", "5")
    monkeypatch.setenv("MAX_TOOL_OUTPUT_CHARS", "1234")
    config = Config.from_env(workspace=str(tmp_path))
    assert config.model == "deepseek-chat"
    assert config.base_url == "https://api.deepseek.com"
    assert config.api_key == "sk-test"
    assert config.max_steps == 7
    assert config.bash_timeout == 5.0
    assert config.max_output_chars == 1234


def test_cli_overrides_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "from-env")
    config = Config.from_env(workspace=str(tmp_path), model="from-flag", max_steps=3)
    assert config.model == "from-flag"
    assert config.max_steps == 3


def test_missing_workspace_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        Config.from_env(workspace=str(tmp_path / "nope"))


def test_workspace_must_be_a_directory(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")
    with pytest.raises(ConfigError, match="not a directory"):
        Config.from_env(workspace=str(file_path))


def test_bad_numbers_are_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_STEPS", "many")
    with pytest.raises(ConfigError, match="MAX_STEPS must be an integer"):
        Config.from_env(workspace=str(tmp_path))


def test_system_prompt_mentions_workspace_and_principles(tmp_path):
    prompt = build_system_prompt(tmp_path)
    assert str(tmp_path) in prompt
    assert "autonomously" in prompt
    assert "Verify" in prompt or "verify" in prompt
    assert "tool" in prompt
