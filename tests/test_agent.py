import json

from helpers import FakeModel, RecordingSink, call, make_agent, text_turn, tool_turn

from transparent_agent import events
from transparent_agent.agent import CANCELLED, ERROR, FINAL, MAX_STEPS
from transparent_agent.models import ModelError, ModelResponse, TextDelta, Usage
from transparent_agent.tools import Tool, ToolRegistry, build_registry


def test_tool_call_then_final_answer(workspace):
    agent = make_agent(workspace, [
        tool_turn("running a command", call("c1", "bash", '{"command": "echo hi"}')),
        text_turn("all done"),
    ])
    result = agent.send("run something")

    assert result.stop_reason == FINAL
    assert result.text == "all done"
    assert result.steps == 2
    assert [message.role for message in agent.history] == ["user", "assistant", "tool", "assistant"]
    assert agent.history[1].tool_calls[0].id == "c1"
    assert agent.history[2].tool_call_id == "c1"
    assert "hi" in agent.history[2].content
    # the second request contains the tool result
    assert [message.role for message in agent.model.requests[1]["messages"]] == ["user", "assistant", "tool"]


def test_multiple_tool_calls_in_one_response(workspace):
    agent = make_agent(workspace, [
        tool_turn("", call("a", "bash", '{"command": "echo one"}'), call("b", "bash", '{"command": "echo two"}')),
        text_turn("done"),
    ])
    agent.send("go")

    assert [message.role for message in agent.history] == ["user", "assistant", "tool", "tool", "assistant"]
    assert [message.tool_call_id for message in agent.history if message.role == "tool"] == ["a", "b"]
    assert "one" in agent.history[2].content
    assert "two" in agent.history[3].content


def test_many_consecutive_iterations(workspace):
    script = [tool_turn("", call(f"c{i}", "ls", "{}")) for i in range(4)] + [text_turn("ok")]
    agent = make_agent(workspace, script)
    result = agent.send("go")
    assert result.steps == 5
    assert result.stop_reason == FINAL


def test_tool_errors_are_observations(workspace):
    agent = make_agent(workspace, [
        tool_turn("", call("c1", "read", '{"path": "missing.py"}'), call("c2", "nosuchtool", "{}")),
        text_turn("recovered"),
    ])
    result = agent.send("go")

    assert result.stop_reason == FINAL
    tool_messages = [message.content for message in agent.history if message.role == "tool"]
    assert all(content.startswith("Error:") for content in tool_messages)
    assert "not found" in tool_messages[0]
    assert "unknown tool" in tool_messages[1]


def test_invalid_json_arguments_are_reported_to_the_model(workspace):
    agent = make_agent(workspace, [tool_turn("", call("c1", "read", "{oops")), text_turn("ok")])
    agent.send("go")
    tool_message = next(message for message in agent.history if message.role == "tool")
    assert "not valid JSON" in tool_message.content


def test_step_limit_terminates_cleanly(workspace):
    script = [tool_turn("", call(f"c{i}", "ls", "{}")) for i in range(5)]
    agent = make_agent(workspace, script, max_steps=3)
    result = agent.send("go")

    assert result.stop_reason == MAX_STEPS
    assert result.steps == 3
    assert "step limit" in result.error
    assert agent.history[-1].role == "tool"  # every call is answered


def test_model_error_leaves_history_untouched(workspace):
    class Broken(FakeModel):
        def stream(self, *, system, messages, tools):
            raise ModelError("connection reset")

    agent = make_agent(workspace, [])
    agent.model = Broken()
    result = agent.send("hi")

    assert result.stop_reason == ERROR
    assert "connection reset" in result.error
    assert [message.role for message in agent.history] == ["user"]


def test_stream_without_complete_response_is_an_error(workspace):
    agent = make_agent(workspace, [[TextDelta("partial answer")]])
    result = agent.send("hi")
    assert result.stop_reason == ERROR
    assert "without a complete response" in result.error


def test_interrupt_during_model_stream_discards_partial_answer(workspace):
    class Interrupted(FakeModel):
        def stream(self, *, system, messages, tools):
            yield TextDelta("half an ans")
            raise KeyboardInterrupt

    agent = make_agent(workspace, [])
    agent.model = Interrupted()
    result = agent.send("hi")

    assert result.stop_reason == CANCELLED
    assert [message.role for message in agent.history] == ["user"]


def test_interrupt_during_tool_keeps_history_valid(workspace):
    def interrupt(arguments, context):
        raise KeyboardInterrupt

    registry = ToolRegistry([Tool(name="slow", description="", parameters={}, run=interrupt)])
    agent = make_agent(
        workspace,
        [tool_turn("", call("a", "slow", "{}"), call("b", "slow", "{}"))],
        tools=registry,
    )
    result = agent.send("go")

    assert result.stop_reason == CANCELLED
    assert [message.role for message in agent.history] == ["user", "assistant", "tool", "tool"]
    assert all("cancelled" in message.content for message in agent.history if message.role == "tool")


def test_interrupt_escaping_through_a_sink_still_leaves_valid_history(workspace):
    class InterruptingSink(RecordingSink):
        def emit(self, event):
            super().emit(event)
            if event.type == events.TOOL_CALL_END:
                raise KeyboardInterrupt

    agent = make_agent(
        workspace,
        [tool_turn("", call("a", "bash", '{"command": "echo hi"}'), call("b", "bash", '{"command": "echo hi"}'))],
        sink=InterruptingSink(),
    )
    result = agent.send("go")

    assert result.stop_reason == CANCELLED
    assert [message.role for message in agent.history] == ["user", "assistant", "tool", "tool"]
    assert agent.history[-1].tool_call_id == "b"


def test_event_sequence_and_payloads(workspace):
    sink = RecordingSink()
    agent = make_agent(
        workspace,
        [tool_turn("working", call("c1", "bash", '{"command": "echo hi"}')), text_turn("done")],
        sink=sink,
    )
    agent.send("go")

    types = sink.types
    assert types[0] == events.AGENT_START
    assert types[1] == events.USER_MESSAGE
    assert types[2] == events.MODEL_REQUEST_START
    assert events.MODEL_TEXT_DELTA in types
    assert types.count(events.MODEL_REQUEST_START) == 2
    assert types.count(events.TOOL_CALL_START) == 1
    assert types.count(events.TOOL_CALL_END) == 1
    assert types[-1] == events.AGENT_FINISH

    start = sink.of_type(events.TOOL_CALL_START)[0].data
    assert start["name"] == "bash" and start["call_id"] == "c1"
    end = sink.of_type(events.TOOL_CALL_END)[0].data
    assert "hi" in end["output"] and end["is_error"] is False and end["duration_s"] >= 0

    request = sink.of_type(events.MODEL_REQUEST_START)[0].data
    assert request["step"] == 1
    assert request["messages"][0]["role"] == "user"
    assert "bash" in [tool["name"] for tool in request["tools"]]

    finish = sink.of_type(events.AGENT_FINISH)[0].data
    assert finish["stop_reason"] == "final" and finish["steps"] == 2


def test_every_event_payload_is_json_serializable(workspace):
    sink = RecordingSink()
    agent = make_agent(
        workspace,
        [tool_turn("", call("c1", "bash", '{"command": "echo hi"}')), text_turn("done")],
        sink=sink,
    )
    agent.send("go")
    for event in sink.events:
        assert json.loads(json.dumps(event.data)) == event.data


def test_agent_behaves_the_same_without_a_sink(workspace):
    def build(sink):
        return make_agent(
            workspace,
            [tool_turn("", call("c1", "ls", "{}")), text_turn("done")],
            sink=sink,
        )

    plain = build(None)
    observed = build(RecordingSink())
    result_plain = plain.send("go")
    result_observed = observed.send("go")

    assert result_plain.stop_reason == result_observed.stop_reason == FINAL
    assert result_plain.steps == result_observed.steps
    assert [message.role for message in plain.history] == [message.role for message in observed.history]
    assert result_plain.text == result_observed.text


def test_broken_sink_does_not_affect_the_agent(workspace, capsys):
    class Broken:
        def emit(self, event):
            raise RuntimeError("sink is broken")

    agent = make_agent(workspace, [text_turn("fine")], sink=Broken())
    result = agent.send("go")
    assert result.stop_reason == FINAL and result.text == "fine"
    assert "event sink failed" in capsys.readouterr().err


def test_system_prompt_and_tools_are_sent_on_every_request(workspace):
    agent = make_agent(
        workspace,
        [tool_turn("", call("c", "ls", "{}")), text_turn("ok")],
        system_prompt="PROMPT-X",
    )
    agent.send("go")
    assert all(request["system"] == "PROMPT-X" for request in agent.model.requests)
    assert [tool.name for tool in agent.model.requests[1]["tools"]] == [
        "read", "write", "edit", "ls", "find", "grep", "bash",
    ]


def test_usage_is_accumulated_across_steps(workspace):
    agent = make_agent(workspace, [
        [ModelResponse(text="", tool_calls=[call("c", "ls", "{}")], usage=Usage(10, 5, 15))],
        [ModelResponse(text="ok", tool_calls=[], usage=Usage(20, 7, 27))],
    ])
    result = agent.send("go")
    assert result.usage.total_tokens == 42
    assert agent.total_usage.total_tokens == 42


def test_reasoning_is_kept_on_the_assistant_message(workspace):
    agent = make_agent(workspace, [
        [ModelResponse(text="answer", tool_calls=[], reasoning="because reasons")],
    ])
    agent.send("go")
    assert agent.history[1].reasoning == "because reasons"


def test_clear_resets_the_conversation(workspace):
    agent = make_agent(workspace, [text_turn("a"), text_turn("b")])
    agent.send("one")
    agent.clear()
    agent.send("two")
    assert [message.role for message in agent.history] == ["user", "assistant"]
    assert agent.history[0].content == "two"


def test_tool_schemas_are_well_formed():
    for tool in build_registry().specs():
        assert tool.parameters["type"] == "object"
        assert isinstance(tool.parameters["properties"], dict)
        assert tool.description
        for required in tool.parameters.get("required", []):
            assert required in tool.parameters["properties"], f"{tool.name}: {required}"
