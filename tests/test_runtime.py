"""Exercise composed resources, actual tools, storage and resumed context."""

import json
from pathlib import Path

import pytest

from helpers import FakeModel, call, text_turn, tool_turn
from transparent_agent.config import Config
from transparent_agent.events import CompositeSink, Event
from transparent_agent.models import Message
from transparent_agent.runtime import Runtime, RuntimeOptions
from transparent_agent.sessions import SessionStore
from transparent_agent.storage import PersistenceError
from transparent_agent.trace import read_trace


def make_skill(root, name="review-code", *, disabled=False):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Review behavior\ndisable-model-invocation: {str(disabled).lower()}\n---\n"
        "BODY_ONLY_AFTER_LOADING\n", encoding="utf-8")
    return directory


def setup(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    return workspace, Config(workspace, "fake-model", None, None), RuntimeOptions(state_dir=tmp_path / "state")


def test_complete_run_has_lazy_skill_full_tool_output_and_resumable_history(tmp_path):
    workspace, config, options = setup(tmp_path)
    (workspace / "AGENTS.md").write_text("PROJECT_RULE")
    skill = make_skill(workspace / ".agents/skills")
    model = FakeModel([
        tool_turn("", call("skill", "load_skill", '{"name":"review-code"}')),
        tool_turn("", call("file", "write", '{"path":"result.txt","content":"verified"}'),
                  call("shell", "bash", '{"command":"seq 1 10000"}')),
        text_turn("done"),
    ])
    config.max_output_chars = 500
    with Runtime(config, options=options, model=model, home=tmp_path / "home") as runtime:
        assert "PROJECT_RULE" in runtime.agent.system_prompt
        assert "BODY_ONLY_AFTER_LOADING" not in runtime.agent.system_prompt
        result = runtime.agent.send("finish the task")
        assert result.stop_reason == "final"
        session, trace = runtime.session_path, runtime.trace_path
        assert "BODY_ONLY_AFTER_LOADING" in model.requests[1]["messages"][-1].content
        assert (workspace / "result.txt").read_text() == "verified"
    records = list(read_trace(trace))
    output = next(r["data"] for r in records if r["type"] == "tool_call_end" and r["data"]["name"] == "bash")
    assert output["truncated"]
    assert len(output["full_output"]) > 30_000 and "5000\n5001" in output["full_output"]
    assert len(output["output"]) < 600
    assert output["metadata"]["stdout"].endswith("10000\n")
    assert next(r for r in records if r["type"] == "skill_loaded")["data"]["path"] == str(skill / "SKILL.md")
    old_count = len(records)
    (workspace / "AGENTS.md").write_text("NEW_PROJECT_RULE")
    followup = FakeModel([text_turn("continued")])
    with Runtime(config, options=RuntimeOptions(resume=session), model=followup, home=tmp_path / "home") as resumed:
        assert "PROJECT_RULE" in resumed.agent.system_prompt and "NEW_PROJECT_RULE" not in resumed.agent.system_prompt
        assert resumed.agent.send("continue").text == "continued"
    continued = list(read_trace(trace))
    assert len(continued) > old_count
    runs = {r["run_id"] for r in continued if r["type"] == "agent_start"}
    assert len(runs) == 2
    assert [r["seq"] for r in continued] == list(range(1, len(continued) + 1))
    assert len(followup.requests[0]["messages"]) > 2


def test_crash_pending_call_is_repaired_without_reexecution(tmp_path):
    workspace, config, options = setup(tmp_path)
    with Runtime(config, options=options, model=FakeModel(), home=tmp_path / "home") as runtime:
        runtime.agent.history = [Message("user", "write"), Message("assistant", tool_calls=[call("pending", "write", '{"path":"marker","content":"wrong"}')])]
        runtime.agent._save()
        session, trace = runtime.session_path, runtime.trace_path
    with Runtime(config, options=RuntimeOptions(resume=session), model=FakeModel(), home=tmp_path / "home") as resumed:
        assert resumed.agent.history[-1].tool_call_id == "pending"
        assert "not replayed" in resumed.agent.history[-1].content
        assert not (workspace / "marker").exists()
    assert any(r["type"] == "session_repaired" for r in read_trace(trace))


def test_session_lock_and_bad_workspace_are_explicit(tmp_path):
    workspace, config, options = setup(tmp_path)
    with Runtime(config, options=options, model=FakeModel(), home=tmp_path / "home") as runtime:
        with pytest.raises(PersistenceError, match="locked"):
            SessionStore(runtime.session_path)
        session = runtime.session_path
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    with pytest.raises(PersistenceError, match="workspace"):
        Runtime(Config(wrong, "m", None, None), options=RuntimeOptions(resume=session), model=FakeModel())


def test_clear_and_model_switch_are_persistent(tmp_path):
    workspace, config, options = setup(tmp_path)
    with Runtime(config, options=options, model=FakeModel([text_turn("ok")]), home=tmp_path / "home") as runtime:
        runtime.agent.send("hello")
        runtime.agent.set_model("other")
        runtime.agent.clear()
        snapshot = json.loads(runtime.session_path.read_text())
        assert snapshot["model"] == "other" and snapshot["messages"] == []
        assert any(r["type"] == "session_cleared" for r in read_trace(runtime.trace_path))


def test_observer_mutation_cannot_change_model_request_or_other_observer(tmp_path):
    workspace, config, options = setup(tmp_path)
    seen = []
    class Mutates:
        def emit(self, event):
            if event.type == "model_request_start":
                event.data["messages"][0]["content"] = "polluted"
    class Records:
        def emit(self, event):
            if event.type == "model_request_start":
                seen.append(event.data["messages"][0]["content"])
    model = FakeModel([text_turn("ok")])
    with Runtime(config, options=options, model=model, sink=CompositeSink([Mutates(), Records()]), home=tmp_path / "home") as runtime:
        runtime.agent.send("original")
    assert seen == ["original"] and model.requests[0]["messages"][0].content == "original"


def test_trace_failure_stops_before_tool_side_effect(tmp_path):
    workspace, config, options = setup(tmp_path)
    model = FakeModel([tool_turn("", call("c", "write", '{"path":"marker","content":"wrong"}'))])
    with Runtime(config, options=options, model=model, home=tmp_path / "home") as runtime:
        original = runtime.recorder.emit
        def fail_on_tool(event):
            if event.type == "tool_call_start":
                raise PersistenceError("disk full")
            original(event)
        runtime.recorder.emit = fail_on_tool
        with pytest.raises(PersistenceError, match="disk full"):
            runtime.agent.send("go")
        assert not (workspace / "marker").exists()
        assert not runtime.agent._running


def test_atomic_save_failure_preserves_previous_snapshot_and_stops(tmp_path, monkeypatch):
    workspace, config, options = setup(tmp_path)
    model = FakeModel([text_turn("must not run")])
    with Runtime(config, options=options, model=model, home=tmp_path / "home") as runtime:
        previous = runtime.session_path.read_bytes()
        def broken_replace(*args):
            raise OSError("disk failure")
        monkeypatch.setattr("transparent_agent.storage.os.replace", broken_replace)
        with pytest.raises(PersistenceError, match="disk failure"):
            runtime.agent.send("go")
        assert runtime.session_path.read_bytes() == previous
        assert model.requests == []
        assert list(runtime.session_path.parent.glob(".session-*")) == []


def test_bad_version_and_missing_trace_refuse_resume(tmp_path):
    workspace, config, options = setup(tmp_path)
    with Runtime(config, options=options, model=FakeModel(), home=tmp_path / "home") as runtime:
        session, trace = runtime.session_path, runtime.trace_path
    original = json.loads(session.read_text())
    session.write_text(json.dumps({**original, "schema_version": 2}))
    with pytest.raises(PersistenceError, match="version"):
        Runtime(config, options=RuntimeOptions(resume=session), model=FakeModel())
    session.write_text(json.dumps(original))
    trace.unlink()
    with pytest.raises(PersistenceError, match="trace is missing"):
        Runtime(config, options=RuntimeOptions(resume=session), model=FakeModel())
