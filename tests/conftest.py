import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from transparent_agent.tools import ToolContext  # noqa: E402


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def ctx(workspace: Path) -> ToolContext:
    return ToolContext(
        workspace=workspace,
        bash_timeout=10.0,
        max_bash_timeout=20.0,
        max_output_chars=30_000,
    )
