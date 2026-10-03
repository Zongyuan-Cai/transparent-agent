"""Configuration, taken from environment variables, .env files and CLI overrides."""

from __future__ import annotations

import os
import math
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping
from urllib.parse import urlsplit

DEFAULT_MODEL = "gpt-4o"


class ConfigError(Exception):
    pass


def user_env_file() -> Path:
    return Path.home() / ".config" / "transparent-agent" / ".env"


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines: comments, blank lines, quotes and `export` prefixes."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key:
            continue
        value = _strip_inline_comment(value)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing ` # comment`, unless the whole value is quoted."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value


def dotenv_paths(cwd: Path | None = None) -> list[Path]:
    """Existing .env files to read, highest precedence first.

    A .env in the directory the agent is started from wins over the user-level
    configuration file.
    """
    cwd = Path.cwd() if cwd is None else Path(cwd)
    candidates = [cwd / ".env", user_env_file()]
    paths: list[Path] = []
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve() not in {path.resolve() for path in paths}:
            paths.append(candidate)
    return paths


def load_dotenv(env: MutableMapping[str, str] | None = None, *, cwd: Path | None = None) -> list[Path]:
    """Load .env files into the environment. Real environment variables win.

    Returns the files that were read.
    """
    target = os.environ if env is None else env
    loaded: list[Path] = []
    for path in dotenv_paths(cwd):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for key, value in parse_dotenv(text).items():
            target.setdefault(key, value)
        loaded.append(path)
    return loaded


@dataclass
class Config:
    workspace: Path
    model: str
    api_key: str | None
    base_url: str | None
    max_steps: int = 40
    bash_timeout: float = 120.0
    max_bash_timeout: float = 600.0
    max_output_chars: int = 30_000
    request_timeout: float = 300.0
    stream_usage: bool = True

    def __post_init__(self):
        self.workspace = Path(self.workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ConfigError(f"Workspace is not a directory: {self.workspace}")
        for field, label in (("max_steps", "MAX_STEPS"), ("max_output_chars", "MAX_TOOL_OUTPUT_CHARS")):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ConfigError(f"{label} must be a positive integer")
        for field, label in (("bash_timeout", "BASH_TIMEOUT_S"), ("max_bash_timeout", "MAX_BASH_TIMEOUT_S"),
                             ("request_timeout", "MODEL_TIMEOUT_S")):
            value = getattr(self, field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ConfigError(f"{label} must be a finite positive number")
        if self.bash_timeout > self.max_bash_timeout:
            raise ConfigError("BASH_TIMEOUT_S must not exceed MAX_BASH_TIMEOUT_S")
        if self.base_url:
            try:
                url = urlsplit(self.base_url)
            except ValueError as exc:
                raise ConfigError("OPENAI_BASE_URL must be an http(s) URL") from exc
            if url.scheme not in ("http", "https") or not url.netloc:
                raise ConfigError("OPENAI_BASE_URL must be an http(s) URL")

    @classmethod
    def from_env(cls, *, workspace: str | None = None, model: str | None = None,
                 base_url: str | None = None, max_steps: int | None = None) -> "Config":
        env = os.environ
        workspace_path = Path(workspace or env.get("WORKSPACE_DIR") or os.getcwd()).expanduser()
        if not workspace_path.exists():
            raise ConfigError(f"Workspace does not exist: {workspace_path}")
        if not workspace_path.is_dir():
            raise ConfigError(f"Workspace is not a directory: {workspace_path}")
        return cls(
            workspace=workspace_path.resolve(),
            model=model or env.get("OPENAI_MODEL") or DEFAULT_MODEL,
            api_key=env.get("OPENAI_API_KEY"),
            base_url=base_url if base_url is not None else env.get("OPENAI_BASE_URL"),
            max_steps=max_steps if max_steps is not None else _env_int(env, "MAX_STEPS", 40),
            bash_timeout=_env_float(env, "BASH_TIMEOUT_S", 120.0),
            max_bash_timeout=_env_float(env, "MAX_BASH_TIMEOUT_S", 600.0),
            max_output_chars=_env_int(env, "MAX_TOOL_OUTPUT_CHARS", 30_000),
            request_timeout=_env_float(env, "MODEL_TIMEOUT_S", 300.0),
            stream_usage=_env_bool(env, "STREAM_USAGE", True),
        )


def _env_int(env, name: str, default: int) -> int:
    raw = env.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(env, name: str, default: float) -> float:
    raw = env.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _env_bool(env, name: str, default: bool) -> bool:
    raw = env.get(name)
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
