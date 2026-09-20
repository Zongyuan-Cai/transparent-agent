import pytest

from transparent_agent.tools.base import ToolError
from transparent_agent.tools.search import (
    MAX_FIND_RESULTS,
    MAX_GREP_MATCHES,
    _find_matches,
    _grep_python,
    tool_find,
    tool_grep,
)


def build_tree(root):
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("parse_config")
    (root / "src" / "parser.py").write_text(
        "def parse_config(path):\n    return path\n\n\ndef parse_value(raw):\n    return raw.strip()\n"
    )
    (root / "src" / "other.py").write_text("import os\n")
    (root / "tests" / "test_parser.py").write_text("from parser import parse_config\n\ndef test_x():\n    assert parse_config\n")


# -- find ------------------------------------------------------------------

def test_find_by_basename_pattern(workspace, ctx):
    build_tree(workspace)
    result = tool_find({"pattern": "*.py"}, ctx)
    assert "src/parser.py" in result.output
    assert "tests/test_parser.py" in result.output
    assert "4 match" in result.output.splitlines()[0] or "match(es)" in result.output.splitlines()[0]


def test_find_skips_git_directory(workspace, ctx):
    build_tree(workspace)
    output = tool_find({"pattern": "config"}, ctx).output
    assert "no files or directories matching" in output


def test_find_type_filter(workspace, ctx):
    build_tree(workspace)
    directories = tool_find({"pattern": "s*", "type": "dir"}, ctx).output
    assert "src/" in directories
    files = tool_find({"pattern": "s*", "type": "file"}, ctx).output
    assert "src/" not in files


def test_find_path_pattern(workspace, ctx):
    build_tree(workspace)
    assert "tests/test_parser.py" in tool_find({"pattern": "**/test_*.py"}, ctx).output
    assert "src/parser.py" not in tool_find({"pattern": "**/test_*.py"}, ctx).output


def test_find_no_matches_is_not_an_error(workspace, ctx):
    build_tree(workspace)
    result = tool_find({"pattern": "*.rs"}, ctx)
    assert not result.is_error
    assert "no files or directories matching" in result.output


def test_find_truncates(workspace, ctx):
    for index in range(MAX_FIND_RESULTS + 5):
        (workspace / f"file{index:03d}.txt").write_text("x")
    output = tool_find({"pattern": "*.txt"}, ctx).output
    assert f"stopped at {MAX_FIND_RESULTS} matches" in output


def test_find_rejects_invalid_type(workspace, ctx):
    with pytest.raises(ToolError, match="'type' must be one of"):
        tool_find({"pattern": "*", "type": "socket"}, ctx)


def test_find_matching_rules():
    from pathlib import Path

    root = Path("/repo")
    assert _find_matches("*.py", "a.py", root / "src/a.py", root)
    assert not _find_matches("*.rs", "a.py", root / "src/a.py", root)
    assert _find_matches("src/*.py", "a.py", root / "src/a.py", root)
    assert not _find_matches("src/*.py", "a.py", root / "lib/a.py", root)


# -- grep ------------------------------------------------------------------

def test_grep_finds_symbols_with_line_numbers(workspace, ctx):
    build_tree(workspace)
    result = tool_grep({"pattern": "parse_config"}, ctx)
    assert not result.is_error
    assert "src/parser.py:1:" in result.output
    assert "tests/test_parser.py:1:" in result.output


def test_grep_no_matches_is_not_an_error(workspace, ctx):
    build_tree(workspace)
    result = tool_grep({"pattern": "does_not_exist_anywhere"}, ctx)
    assert not result.is_error
    assert "no matches for" in result.output


def test_grep_glob_filter(workspace, ctx):
    build_tree(workspace)
    output = tool_grep({"pattern": "parse_config", "glob": "*.py", "path": "tests"}, ctx).output
    assert "tests/test_parser.py" in output
    assert "src/parser.py" not in output


def test_grep_ignore_case(workspace, ctx):
    build_tree(workspace)
    assert "src/parser.py" not in tool_grep({"pattern": "PARSE_CONFIG"}, ctx).output
    assert "src/parser.py" in tool_grep({"pattern": "PARSE_CONFIG", "ignore_case": True}, ctx).output


def test_grep_invalid_regex(workspace, ctx):
    with pytest.raises(ToolError, match="invalid regular expression"):
        tool_grep({"pattern": "("}, ctx)


def test_grep_missing_path(workspace, ctx):
    with pytest.raises(ToolError, match="not found"):
        tool_grep({"pattern": "x", "path": "nope"}, ctx)


def test_grep_single_file(workspace, ctx):
    build_tree(workspace)
    output = tool_grep({"pattern": "parse_value", "path": "src/parser.py"}, ctx).output
    assert "src/parser.py:5:" in output


def test_grep_truncates(workspace, ctx):
    (workspace / "many.txt").write_text("needle\n" * (MAX_GREP_MATCHES + 20))
    output = tool_grep({"pattern": "needle"}, ctx).output
    assert f"output limited to the first {MAX_GREP_MATCHES}" in output


def test_python_fallback_matches_public_output(workspace, ctx):
    """The fallback used when ripgrep is unavailable behaves like the tool."""
    build_tree(workspace)
    matches, note = _grep_python("parse_config", workspace, None, False, ctx)
    assert note == ""
    assert any(line.startswith("src/parser.py:1:") for line in matches)
    assert not any(".git" in line for line in matches)


def test_python_fallback_skips_binary_files(workspace, ctx):
    (workspace / "blob.bin").write_bytes(b"needle\x00needle")
    (workspace / "text.txt").write_text("needle\n")
    matches, _ = _grep_python("needle", workspace, None, False, ctx)
    assert matches == ["text.txt:1:needle"]
