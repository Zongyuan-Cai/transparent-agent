from pathlib import Path

from transparent_agent.config import dotenv_paths, load_dotenv, parse_dotenv


def test_parse_dotenv_handles_comments_quotes_and_export():
    values = parse_dotenv("""
# a comment
OPENAI_MODEL=deepseek-flash

OPENAI_BASE_URL = "https://api.deepseek.com"   # inline comment
export OPENAI_API_KEY='sk-quoted'
NOT_A_SETTING
MAX_STEPS=12
""")
    assert values == {
        "OPENAI_MODEL": "deepseek-flash",
        "OPENAI_BASE_URL": "https://api.deepseek.com",
        "OPENAI_API_KEY": "sk-quoted",
        "MAX_STEPS": "12",
    }


def test_parse_dotenv_keeps_hashes_inside_values():
    assert parse_dotenv("TOKEN=abc#def")["TOKEN"] == "abc#def"


def use_fake_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))


def test_user_env_file_is_loaded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config/transparent-agent").mkdir(parents=True)
    (home / ".config/transparent-agent/.env").write_text("OPENAI_MODEL=deepseek-flash\n")
    use_fake_home(monkeypatch, home)

    target: dict[str, str] = {}
    loaded = load_dotenv(target, cwd=tmp_path)
    assert loaded == [home / ".config/transparent-agent/.env"]
    assert target["OPENAI_MODEL"] == "deepseek-flash"


def test_project_env_overrides_user_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config/transparent-agent").mkdir(parents=True)
    (home / ".config/transparent-agent/.env").write_text("OPENAI_MODEL=user-level\n")
    use_fake_home(monkeypatch, home)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("OPENAI_MODEL=project-level\n")

    target: dict[str, str] = {}
    load_dotenv(target, cwd=project)
    assert target["OPENAI_MODEL"] == "project-level"


def test_real_environment_wins(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config/transparent-agent").mkdir(parents=True)
    (home / ".config/transparent-agent/.env").write_text("OPENAI_MODEL=from-file\n")
    use_fake_home(monkeypatch, home)

    target = {"OPENAI_MODEL": "from-shell"}
    load_dotenv(target, cwd=tmp_path)
    assert target["OPENAI_MODEL"] == "from-shell"


def test_missing_files_are_ignored(tmp_path, monkeypatch):
    use_fake_home(monkeypatch, tmp_path / "nowhere")
    assert dotenv_paths(tmp_path) == []
    assert load_dotenv({}, cwd=tmp_path) == []


def test_repo_env_is_not_read_twice(tmp_path, monkeypatch):
    """A .env in the current directory is not loaded twice."""
    (tmp_path / ".env").write_text("OPENAI_MODEL=x\n")
    use_fake_home(monkeypatch, tmp_path)
    paths = dotenv_paths(tmp_path)
    assert len(set(path.resolve() for path in paths)) == len(paths)
