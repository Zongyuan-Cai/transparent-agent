import base64
import os
import signal
import threading
import time

import pytest

from helpers import FakeModel, make_agent, text_turn
from transparent_agent.cli import main
from transparent_agent.config import Config, ConfigError
from transparent_agent.tools import Tool, ToolRegistry, ToolResult, build_registry
from transparent_agent.tools.base import ToolInterrupted
from transparent_agent.tools.fs import tool_read
from transparent_agent.tools.shell import tool_bash


@pytest.mark.parametrize("field,value", [("max_steps", 0), ("max_steps", -1), ("max_output_chars", 0),
                                         ("request_timeout", float("nan")), ("bash_timeout", float("inf"))])
def test_invalid_runtime_limits_fail_early(workspace, field, value):
    with pytest.raises(ConfigError):
        Config(workspace, "m", None, None, **{field: value})


@pytest.mark.parametrize("tool,arguments", [("write", '{"path":"marker","content":1}'),
                                          ("write", '{"path":"marker","content":"x","extra":1}'),
                                          ("edit", '{"path":"marker","old_string":"x","new_string":"y","replace_all":"false"}'),
                                          ("read", '{"path":"marker","start_line":true}'),
                                          ("bash", '{"command":"touch marker","timeout_s":NaN}')])
def test_schema_errors_cannot_cause_side_effects(workspace, ctx, tool, arguments):
    assert build_registry().execute(tool, arguments, ctx).is_error
    assert not (workspace / "marker").exists()


def test_duplicate_tools_are_rejected():
    tool = Tool("same", "", {}, lambda a, c: ToolResult("ok"))
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry([tool, tool])


def test_reentrant_submission_is_rejected_without_corrupting_outer_turn(workspace):
    agent = make_agent(workspace)
    class Reentrant(FakeModel):
        def stream(self, **kwargs):
            with pytest.raises(RuntimeError, match="already running"):
                agent.send("nested")
            yield from text_turn("outer")
    agent.model = Reentrant()
    assert agent.send("original").text == "outer"
    assert agent.history[0].content == "original" and len(agent.history) == 2


def test_shell_keeps_more_than_old_two_megabyte_limit_and_raw_binary(workspace, ctx):
    result = tool_bash({"command": "python3 -c 'import sys; sys.stdout.buffer.write(b\"a\" * 2100000 + b\"\\xffEND\")'"}, ctx)
    raw = base64.b64decode(result.metadata["stdout_base64"])
    assert len(raw) == 2_100_004 and raw.endswith(b"\xffEND")
    assert len(result.output) > 2_100_000


def test_shell_cancellation_keeps_partial_output_and_kills_descendants(workspace, ctx):
    timer = threading.Timer(0.15, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        with pytest.raises(ToolInterrupted) as interrupted:
            tool_bash({"command": "echo BEFORE_CANCEL; sleep 1; touch marker"}, ctx)
        assert "BEFORE_CANCEL" in interrupted.value.result.metadata["stdout"]
    finally:
        timer.cancel()
    time.sleep(1.1)
    assert not (workspace / "marker").exists()


def test_normally_exited_shell_cleans_background_children(workspace, ctx):
    tool_bash({"command": "(sleep 0.4; touch marker) &"}, ctx)
    time.sleep(0.6)
    assert not (workspace / "marker").exists()


def test_python_grep_skips_external_symlink(workspace, ctx, tmp_path, monkeypatch):
    outside = tmp_path / "secret"
    outside.write_text("EXTERNAL_SECRET")
    (workspace / "link").symlink_to(outside)
    monkeypatch.setattr("transparent_agent.tools.search.shutil.which", lambda _: None)
    result = build_registry().execute("grep", '{"pattern":"EXTERNAL_SECRET"}', ctx)
    assert "no matches" in result.output


def test_read_invalid_utf8_and_zero_range_fail(workspace, ctx):
    (workspace / "invalid").write_bytes(b"\xff")
    with pytest.raises(Exception, match="UTF-8"):
        tool_read({"path": "invalid"}, ctx)
    (workspace / "text").write_text("text")
    with pytest.raises(Exception, match=">= 1"):
        tool_read({"path": "text", "start_line": 0}, ctx)


def test_offline_inspect_does_not_require_configuration(tmp_path, monkeypatch, capsys):
    path = tmp_path / "trace.jsonl"
    path.write_text('{"schema_version":1,"seq":1,"type":"example","data":{"content":"中文"}}\n')
    monkeypatch.setattr("transparent_agent.cli.load_dotenv", lambda: (_ for _ in ()).throw(AssertionError("must not configure model")))
    assert main(["--inspect", str(path)]) == 0
    assert "中文" in capsys.readouterr().out
