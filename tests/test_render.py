import io

from transparent_agent import events
from transparent_agent.events import Event
from transparent_agent.tui.render import Renderer, Style, format_tokens, summarize_call


def make_renderer():
    out = io.StringIO()
    return Renderer(style=Style(enabled=False), out=out, log=out), out


def event(event_type, **data):
    return Event(type=event_type, data=data, seq=1, timestamp=0.0)


def test_summarize_call_for_each_tool():
    assert summarize_call("bash", '{"command": "pytest -q"}') == "pytest -q"
    assert summarize_call("read", '{"path": "src/parser.py"}') == "src/parser.py"
    assert summarize_call("read", '{"path": "a.py", "start_line": 10, "end_line": 20}') == "a.py (lines 10-20)"
    assert summarize_call("edit", '{"path": "a.py", "old_string": "x", "new_string": "y"}') == "a.py"
    assert summarize_call("grep", '{"pattern": "parse_config", "glob": "*.py"}') == "/parse_config/ [*.py]"
    assert summarize_call("find", '{"pattern": "*.toml", "path": "src"}') == "*.toml under src"


def test_summarize_call_truncates_and_flattens():
    summary = summarize_call("bash", '{"command": "line one\\nline two"}')
    assert summary == "line one ⏎ line two"
    assert len(summarize_call("bash", '{"command": "' + "x" * 500 + '"}')) <= 110


def test_summarize_call_survives_broken_arguments():
    assert summarize_call("bash", "{not json") == "{not json"


def test_renderer_renders_a_normal_turn():
    renderer, out = make_renderer()
    for item in [
        event(events.MODEL_TEXT_DELTA, step=1, text="Looking at the tests"),
        event(events.MODEL_TEXT_DELTA, step=1, text="."),
        event(events.MODEL_RESPONSE, step=1, text="Looking at the tests.", tool_calls=[], finish_reason="tool_calls"),
        event(events.TOOL_CALL_START, step=1, call_id="c1", name="bash", arguments='{"command": "pytest -q"}'),
        event(events.TOOL_CALL_END, step=1, call_id="c1", name="bash", output="raw output",
              display="8 passed", is_error=False, cancelled=False, duration_s=0.1),
        event(events.AGENT_FINISH, stop_reason="final", steps=2, usage={"total_tokens": 1500}, text="done", error=None),
    ]:
        renderer.emit(item)
    text = out.getvalue()
    assert "● Looking at the tests." in text
    assert "→ bash pytest -q" in text
    assert "8 passed" in text
    assert "raw output" not in text  # the raw tool payload stays out of the transcript
    assert "✓ done (2 steps, 1.5k tokens)" in text


def test_renderer_shows_errors_in_the_transcript():
    renderer, out = make_renderer()
    renderer.emit(event(events.TOOL_CALL_START, step=1, call_id="c", name="read", arguments='{"path": "x"}'))
    renderer.emit(event(events.TOOL_CALL_END, step=1, call_id="c", name="read", output="file not found: x",
                        display=None, is_error=True, cancelled=False, duration_s=0.0))
    renderer.emit(event(events.AGENT_ERROR, stage="model_request", step=2, message="connection reset"))
    text = out.getvalue()
    assert "file not found: x" in text
    assert "connection reset" in text


def test_renderer_reports_interruption_and_step_limit():
    renderer, out = make_renderer()
    renderer.emit(event(events.AGENT_FINISH, stop_reason="cancelled", steps=1, usage=None, text="", error=None))
    renderer.emit(event(events.AGENT_FINISH, stop_reason="max_steps", steps=40, usage=None, text="",
                        error="reached the step limit (40 model requests) before finishing the task"))
    text = out.getvalue()
    assert "⚠ interrupted" in text
    assert "reached the step limit" in text


def test_renderer_ignores_internal_events():
    """Model requests, prompts and tool schemas must not leak into the TUI."""
    renderer, out = make_renderer()
    renderer.emit(event(events.AGENT_START, model="m", workspace="/w", max_steps=40))
    renderer.emit(event(events.USER_MESSAGE, text="hi"))
    renderer.emit(event(events.MODEL_REQUEST_START, step=1, system="SECRET PROMPT",
                        messages=[{"role": "user", "content": "raw context"}], tools=[{"name": "bash"}]))
    renderer.emit(event(events.MODEL_RESPONSE, step=1, text="hi", tool_calls=[], finish_reason="stop"))
    assert out.getvalue() == ""


def test_format_tokens():
    assert format_tokens(999) == "999"
    assert format_tokens(1500) == "1.5k"
    assert format_tokens(2_500_000) == "2.5M"
