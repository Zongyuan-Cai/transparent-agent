"""The OpenAI-compatible adapter, tested against a real local HTTP server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from openai import OpenAI

from transparent_agent.models import Message, ModelError, ModelResponse, TextDelta, ToolCall, ToolSpec
from transparent_agent.providers.openai_chat import OpenAIChatModel


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.requests.append(json.loads(self.rfile.read(length) or b"{}"))
        if self.server.status != 200:
            payload = json.dumps({"error": {"message": "boom", "type": "server_error"}}).encode()
            self.send_response(self.server.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for item in self.server.chunks:
            self.wfile.write(f"data: {json.dumps(item)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.chunks = []
    httpd.requests = []
    httpd.status = 200
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    thread.join(timeout=5)


def make_model(server, **kwargs) -> OpenAIChatModel:
    client = OpenAI(
        api_key="test-key",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        http_client=httpx.Client(trust_env=False),
    )
    return OpenAIChatModel(model="test-model", api_key="test-key", base_url=None, client=client, **kwargs)


def chunk(delta, *, finish=None, usage=None, end=False):
    choices = [] if end else [{"index": 0, "delta": delta, "finish_reason": finish}]
    data = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": choices,
    }
    if usage is not None:
        data["usage"] = usage
    return data


def collect(model, messages=None, tools=None):
    return list(model.stream(
        system="sys",
        messages=messages or [Message(role="user", content="hi")],
        tools=tools or [],
    ))


def test_missing_api_key_is_reported():
    with pytest.raises(ModelError, match="No API key"):
        OpenAIChatModel(model="m", api_key=None, base_url=None)


def test_streams_text_and_returns_complete_response(server):
    server.chunks = [
        chunk({"role": "assistant", "content": "Hel"}),
        chunk({"content": "lo"}),
        chunk({}, finish="stop"),
        chunk({}, end=True, usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}),
    ]
    items = collect(make_model(server))

    deltas = [item.text for item in items if isinstance(item, TextDelta)]
    assert deltas == ["Hel", "lo"]
    response = items[-1]
    assert isinstance(response, ModelResponse)
    assert response.text == "Hello"
    assert response.tool_calls == []
    assert response.finish_reason == "stop"
    assert response.usage.total_tokens == 5

    request = server.requests[0]
    assert request["messages"][0] == {"role": "system", "content": "sys"}
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert "tools" not in request


def test_reassembles_streamed_tool_call_fragments(server):
    server.chunks = [
        chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                               "function": {"name": "bash", "arguments": ""}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"command":'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "ls"}'}}]}),
        chunk({}, finish="tool_calls"),
    ]
    response = collect(make_model(server))[-1]
    assert response.tool_calls == [ToolCall(id="call_1", name="bash", arguments='{"command": "ls"}')]
    assert response.finish_reason == "tool_calls"


def test_handles_several_tool_calls_in_one_response(server):
    server.chunks = [
        chunk({"tool_calls": [
            {"index": 0, "id": "a", "function": {"name": "read", "arguments": '{"path": "x"}'}},
            {"index": 1, "id": "b", "function": {"name": "bash", "arguments": '{"command": "ls"}'}},
        ]}),
        chunk({}, finish="tool_calls"),
    ]
    response = collect(make_model(server))[-1]
    assert [(call.id, call.name) for call in response.tool_calls] == [("a", "read"), ("b", "bash")]


def test_captures_provider_reasoning_text(server):
    server.chunks = [
        chunk({"reasoning_content": "thinking hard"}),
        chunk({"content": "answer"}),
        chunk({}, finish="stop"),
    ]
    response = collect(make_model(server))[-1]
    assert response.reasoning == "thinking hard"
    assert response.text == "answer"


def test_encodes_history_for_the_request(server):
    server.chunks = [chunk({"content": "ok"}, finish="stop")]
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content=None,
                tool_calls=[ToolCall(id="c1", name="bash", arguments='{"command": "ls"}')],
                reasoning="because"),
        Message(role="tool", content="the output", tool_call_id="c1"),
    ]
    collect(make_model(server), messages=messages)

    sent = server.requests[0]["messages"]
    assert sent[0] == {"role": "system", "content": "sys"}
    assert sent[1] == {"role": "user", "content": "hi"}
    assert sent[2]["tool_calls"][0]["function"] == {"name": "bash", "arguments": '{"command": "ls"}'}
    assert sent[2]["tool_calls"][0]["id"] == "c1"
    assert sent[2]["reasoning_content"] == "because"
    assert sent[3] == {"role": "tool", "tool_call_id": "c1", "content": "the output"}


def test_tools_are_sent_in_openai_format(server):
    server.chunks = [chunk({"content": "ok"}, finish="stop")]
    spec = ToolSpec(name="read", description="Read a file", parameters={"type": "object", "properties": {}})
    collect(make_model(server), tools=[spec])
    sent = server.requests[0]["tools"]
    assert sent == [{"type": "function", "function": {"name": "read", "description": "Read a file",
                                                      "parameters": {"type": "object", "properties": {}}}}]


def test_usage_can_be_disabled(server):
    server.chunks = [chunk({"content": "ok"}, finish="stop")]
    collect(make_model(server, stream_usage=False))
    assert "stream_options" not in server.requests[0]


def test_http_error_becomes_model_error(server):
    server.status = 500
    with pytest.raises(ModelError, match="Model request failed"):
        collect(make_model(server))


def test_connection_failure_becomes_model_error():
    client = OpenAI(api_key="test-key", base_url="http://127.0.0.1:9/v1",
                    http_client=httpx.Client(trust_env=False), max_retries=0)
    model = OpenAIChatModel(model="test-model", api_key="test-key", base_url=None, client=client)
    with pytest.raises(ModelError, match="Model request failed"):
        collect(model)
