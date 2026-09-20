"""The ripgrep code path, exercised through a stub `rg` on PATH.

ripgrep is not installed everywhere, so this keeps the fast path tested.
"""

import json
import os
import stat

import pytest

from transparent_agent.tools.base import ToolError
from transparent_agent.tools.search import MAX_GREP_MATCHES, tool_grep


def install_stub_rg(monkeypatch, tmp_path, script: str) -> None:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "rg"
    stub.write_text("#!/bin/sh\n" + script)
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def emit(lines, exit_code: int = 0) -> str:
    """A stub that prints the given lines, then exits with the given code."""
    body = "cat <<'RGEOF'\n" + "".join(line + "\n" for line in lines) + "RGEOF\n"
    return body + (f"exit {exit_code}\n" if exit_code else "")


def rg_match(path: str, line_number: int, text: str) -> str:
    return json.dumps({
        "type": "match",
        "data": {
            "path": {"text": path},
            "lines": {"text": text + "\n"},
            "line_number": line_number,
            "absolute_offset": 0,
            "submatches": [],
        },
    })


def test_ripgrep_matches_are_parsed(workspace, ctx, tmp_path, monkeypatch):
    target = workspace / "src" / "parser.py"
    target.parent.mkdir()
    target.write_text("def parse_config(path):\n")
    install_stub_rg(monkeypatch, tmp_path, emit([
        json.dumps({"type": "begin", "data": {"path": {"text": str(target)}}}),
        rg_match(str(target), 1, "def parse_config(path):"),
        json.dumps({"type": "end", "data": {"path": {"text": str(target)}, "stats": {}}}),
        json.dumps({"type": "summary", "data": {"elapsed_total": {"secs": 0, "nanos": 1}}}),
    ]))

    result = tool_grep({"pattern": "parse_config"}, ctx)
    assert not result.is_error
    assert "1 match(es) for 'parse_config' in .:" in result.output
    assert "src/parser.py:1:def parse_config(path):" in result.output


def test_ripgrep_no_matches_is_not_an_error(workspace, ctx, tmp_path, monkeypatch):
    install_stub_rg(monkeypatch, tmp_path, emit([json.dumps({"type": "summary", "data": {}})], exit_code=1))
    result = tool_grep({"pattern": "nothing"}, ctx)
    assert not result.is_error
    assert "no matches for 'nothing' in ." in result.output


def test_ripgrep_failure_is_reported(workspace, ctx, tmp_path, monkeypatch):
    install_stub_rg(monkeypatch, tmp_path, "echo 'rg: unreachable' >&2\nexit 2\n")
    with pytest.raises(ToolError, match="ripgrep failed"):
        tool_grep({"pattern": "needle"}, ctx)


def test_ripgrep_output_is_capped(workspace, ctx, tmp_path, monkeypatch):
    target = workspace / "many.txt"
    target.write_text("needle\n")
    lines = [rg_match(str(target), number, "needle") for number in range(MAX_GREP_MATCHES + 50)]
    install_stub_rg(monkeypatch, tmp_path, emit(lines))

    result = tool_grep({"pattern": "needle"}, ctx)
    assert f"output limited to the first {MAX_GREP_MATCHES}" in result.output
    assert result.output.count("many.txt:") == MAX_GREP_MATCHES
