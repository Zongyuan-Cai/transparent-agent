import time

import pytest

from transparent_agent.tools import build_registry
from transparent_agent.tools.base import ToolError
from transparent_agent.tools.shell import tool_bash


def test_bash_captures_stdout_and_exit_code(workspace, ctx):
    result = tool_bash({"command": "echo hello && printf 'oops' >&2"}, ctx)
    assert not result.is_error
    assert "status: completed" in result.output
    assert "exit_code: 0" in result.output
    assert "hello" in result.output
    assert "oops" in result.output
    assert "--- stderr ---" in result.output


def test_bash_nonzero_exit_is_a_normal_observation(workspace, ctx):
    result = tool_bash({"command": "echo out; exit 3"}, ctx)
    assert not result.is_error
    assert "exit_code: 3" in result.output
    assert "out" in result.output


def test_bash_runs_in_workspace_by_default(workspace, ctx):
    result = tool_bash({"command": "pwd"}, ctx)
    assert str(workspace) in result.output


def test_bash_cwd_argument(workspace, ctx):
    (workspace / "sub").mkdir()
    result = tool_bash({"command": "pwd", "cwd": "sub"}, ctx)
    assert str(workspace / "sub") in result.output
    assert "cwd: sub" in result.output


def test_bash_pipes_and_multiline_scripts(workspace, ctx):
    result = tool_bash({"command": "printf 'a\\nb\\nc\\n' | wc -l"}, ctx)
    assert "3" in result.output


def test_bash_does_not_hang_on_stdin(workspace, ctx):
    started = time.monotonic()
    result = tool_bash({"command": "cat", "timeout_s": 5}, ctx)
    assert time.monotonic() - started < 3
    assert "status: completed" in result.output


def test_bash_timeout_kills_the_process_group(workspace, ctx):
    started = time.monotonic()
    result = tool_bash({"command": "sleep 5; touch marker", "timeout_s": 0.5}, ctx)
    duration = time.monotonic() - started
    assert "status: timed_out" in result.output
    assert "killed by signal" in result.output
    assert duration < 3
    time.sleep(1)
    assert not (workspace / "marker").exists(), "timed-out command was still running"


def test_bash_timeout_is_clamped_to_maximum(workspace, ctx):
    result = tool_bash({"command": "true", "timeout_s": 9999}, ctx)
    assert "was clamped" in result.output


def test_bash_rejects_bad_timeout(workspace, ctx):
    with pytest.raises(ToolError, match="positive number"):
        tool_bash({"command": "true", "timeout_s": -1}, ctx)
    with pytest.raises(ToolError, match="positive number"):
        tool_bash({"command": "true", "timeout_s": "soon"}, ctx)


def test_bash_rejects_cwd_outside_workspace(workspace, ctx):
    with pytest.raises(ToolError, match="outside the workspace"):
        tool_bash({"command": "pwd", "cwd": "../.."}, ctx)


def test_bash_rejects_missing_cwd(workspace, ctx):
    with pytest.raises(ToolError, match="cwd not found"):
        tool_bash({"command": "pwd", "cwd": "nowhere"}, ctx)


def test_bash_requires_command(workspace, ctx):
    with pytest.raises(ToolError, match="missing required string argument 'command'"):
        tool_bash({}, ctx)


def test_bash_output_is_truncated_for_the_model(workspace, ctx):
    ctx.max_output_chars = 500
    result = build_registry().execute("bash", '{"command": "seq 1 10000"}', ctx)
    assert "characters omitted" in result.output
    assert len(result.output) < 700


def test_bash_display_omits_metadata(workspace, ctx):
    result = tool_bash({"command": "echo visible"}, ctx)
    assert "visible" in result.display
    assert "exit_code" not in result.display
    assert "--- stdout ---" not in result.display


def test_bash_working_directory_changes_do_not_leak(workspace, ctx):
    (workspace / "sub").mkdir()
    tool_bash({"command": "cd sub"}, ctx)
    assert str(workspace) in tool_bash({"command": "pwd"}, ctx).output
