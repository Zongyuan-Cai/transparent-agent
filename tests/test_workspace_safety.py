"""Filesystem tools must not escape the configured workspace."""

import pytest

from transparent_agent.tools import build_registry
from transparent_agent.tools.base import ToolError
from transparent_agent.tools.fs import tool_edit, tool_read, tool_write
from transparent_agent.tools.search import tool_find, tool_grep


def _secret(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret\n")
    return outside


@pytest.mark.parametrize("path", ["../outside/secret.txt", "../../etc/passwd", "src/../../outside/secret.txt"])
def test_relative_escapes_are_rejected(workspace, ctx, tmp_path, path):
    _secret(tmp_path)
    with pytest.raises(ToolError, match="outside the workspace"):
        tool_read({"path": path}, ctx)


def test_absolute_path_outside_is_rejected(workspace, ctx, tmp_path):
    outside = _secret(tmp_path)
    with pytest.raises(ToolError, match="outside the workspace"):
        tool_read({"path": str(outside / "secret.txt")}, ctx)


def test_home_expansion_is_rejected(workspace, ctx):
    with pytest.raises(ToolError, match="outside the workspace"):
        tool_read({"path": "~/somewhere.txt"}, ctx)


def test_symlink_escape_is_rejected(workspace, ctx, tmp_path):
    outside = _secret(tmp_path)
    (workspace / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ToolError, match="outside the workspace"):
        tool_read({"path": "link/secret.txt"}, ctx)
    with pytest.raises(ToolError, match="outside the workspace"):
        tool_write({"path": "link/new.txt", "content": "x"}, ctx)
    assert not (outside / "new.txt").exists()


def test_symlink_file_escape_is_rejected(workspace, ctx, tmp_path):
    outside = _secret(tmp_path)
    (workspace / "secret-link").symlink_to(outside / "secret.txt")
    with pytest.raises(ToolError, match="outside the workspace"):
        tool_edit({"path": "secret-link", "old_string": "top", "new_string": "bottom"}, ctx)
    assert (outside / "secret.txt").read_text() == "top secret\n"


def test_symlinks_inside_the_workspace_still_work(workspace, ctx):
    (workspace / "real").mkdir()
    (workspace / "real" / "a.txt").write_text("inside\n")
    (workspace / "alias").symlink_to(workspace / "real", target_is_directory=True)
    assert "inside" in tool_read({"path": "alias/a.txt"}, ctx).output


def test_search_tools_cannot_escape(workspace, ctx, tmp_path):
    _secret(tmp_path)
    with pytest.raises(ToolError, match="outside the workspace"):
        tool_grep({"pattern": "secret", "path": "../outside"}, ctx)
    with pytest.raises(ToolError, match="outside the workspace"):
        tool_find({"pattern": "*.txt", "path": "../outside"}, ctx)


def test_absolute_path_inside_the_workspace_is_allowed(workspace, ctx):
    (workspace / "a.txt").write_text("ok\n")
    assert "ok" in tool_read({"path": str(workspace / "a.txt")}, ctx).output


def test_registry_returns_escape_attempts_as_error_results(workspace, ctx, tmp_path):
    _secret(tmp_path)
    result = build_registry().execute("read", '{"path": "../outside/secret.txt"}', ctx)
    assert result.is_error
    assert "outside the workspace" in result.output
    assert "top secret" not in result.output
