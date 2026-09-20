import io

from helpers import FakeModel, make_agent, text_turn

from transparent_agent.models import TextDelta
from transparent_agent.tui.app import Tui
from transparent_agent.tui.render import Renderer, Style


def feed(monkeypatch, lines):
    queue = list(lines)

    def fake_input(*args):
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)


def make_tui(workspace, script=()):
    out = io.StringIO()
    style = Style(enabled=False)
    renderer = Renderer(style=style, out=out, log=out)
    agent = make_agent(workspace, list(script), sink=renderer)
    return Tui(agent, renderer, style, out=out), agent, out


def test_banner_and_one_turn(workspace, monkeypatch):
    tui, agent, out = make_tui(workspace, [text_turn("Fixed the parser.")])
    feed(monkeypatch, ["fix the parser", "/exit"])

    assert tui.run() == 0
    text = out.getvalue()
    assert "Transparent Agent 0.1.0" in text
    assert f"workspace: {workspace}" in text
    assert "model: fake-model" in text
    assert "● Fixed the parser." in text
    assert [message.role for message in agent.history] == ["user", "assistant"]
    assert agent.history[0].content == "fix the parser"


def test_end_of_input_exits_cleanly(workspace, monkeypatch):
    tui, _, _ = make_tui(workspace)
    feed(monkeypatch, [])
    assert tui.run() == 0


def test_empty_input_is_ignored(workspace, monkeypatch):
    tui, agent, _ = make_tui(workspace, [text_turn("ok")])
    feed(monkeypatch, ["", "   ", "do something", "/exit"])
    tui.run()
    assert [message.content for message in agent.history if message.role == "user"] == ["do something"]


def test_multiline_input_with_continuation(workspace, monkeypatch):
    tui, agent, _ = make_tui(workspace, [text_turn("ok")])
    feed(monkeypatch, ["first line \\", "second line", "/exit"])
    tui.run()
    assert agent.history[0].content == "first line\nsecond line"


def test_clear_command(workspace, monkeypatch):
    tui, agent, out = make_tui(workspace, [text_turn("ok")])
    feed(monkeypatch, ["hello", "/clear", "/exit"])
    tui.run()
    assert agent.history == []
    assert "conversation cleared" in out.getvalue()


def test_status_command(workspace, monkeypatch):
    tui, agent, out = make_tui(workspace, [text_turn("ok")])
    feed(monkeypatch, ["hello", "/status", "/exit"])
    tui.run()
    text = out.getvalue()
    assert f"workspace: {workspace}" in text
    assert "messages: 2 (assistant 1, user 1)" in text
    assert "tokens:" in text


def test_model_command_shows_and_changes_the_model(workspace, monkeypatch):
    tui, agent, out = make_tui(workspace)
    feed(monkeypatch, ["/model", "/model other-model", "/exit"])
    tui.run()
    assert agent.model.name == "other-model"
    assert out.getvalue().count("model: fake-model") == 2  # banner + first /model


def test_unknown_command_is_not_sent_to_the_model(workspace, monkeypatch):
    tui, agent, out = make_tui(workspace)
    feed(monkeypatch, ["/bogus", "/exit"])
    tui.run()
    assert agent.history == []
    assert "unknown command" in out.getvalue()


def test_help_command(workspace, monkeypatch):
    tui, _, out = make_tui(workspace)
    feed(monkeypatch, ["/help", "/exit"])
    tui.run()
    assert "/status" in out.getvalue()


def test_interrupted_turn_returns_to_the_prompt(workspace, monkeypatch):
    class Interrupting(FakeModel):
        def stream(self, *, system, messages, tools):
            yield TextDelta("working on it")
            raise KeyboardInterrupt

    tui, agent, out = make_tui(workspace)
    agent.model = Interrupting()
    feed(monkeypatch, ["long task", "/exit"])

    assert tui.run() == 0
    assert "⚠ interrupted" in out.getvalue()
    assert [message.role for message in agent.history] == ["user"]


def test_conversation_continues_across_turns(workspace, monkeypatch):
    tui, agent, _ = make_tui(workspace, [text_turn("first answer"), text_turn("second answer")])
    feed(monkeypatch, ["task one", "task two", "/exit"])
    tui.run()
    assert [message.role for message in agent.history] == ["user", "assistant", "user", "assistant"]
    second_request = agent.model.requests[1]["messages"]
    assert [message.role for message in second_request] == ["user", "assistant", "user"]
