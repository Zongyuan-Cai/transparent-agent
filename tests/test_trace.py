"""Verify actual HTTP bytes, including data the normalized model discards."""

import base64
import json

import httpx
import pytest

from transparent_agent.events import EventBus
from transparent_agent.models import Message, ModelError
from transparent_agent.providers.openai_chat import OpenAIChatModel
from transparent_agent.storage import PersistenceError
from transparent_agent.trace import TraceRecorder, read_trace


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


def frame(delta, finish=None, **extra):
    return {"id": "r", "object": "chat.completion.chunk", "created": 1, "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}


def sse(*frames):
    return b"".join(b"data: " + json.dumps(f, ensure_ascii=False).encode() + b"\n\n" for f in frames) + b"data: [DONE]\n\n"


def decode_chunks(records):
    return b"".join(base64.b64decode(r["data"]["body_base64"]) for r in records if r["type"] == "api_response_chunk")


def test_exact_request_and_raw_response_survive_normalization(tmp_path):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path, "session")
    bus = EventBus(session_id="session", recorder=recorder)
    bus.begin_run()
    bus.request_id = "request"
    body = sse(frame({"reasoning_content": "推理", "content": "你好"}, "stop", unknown_vendor={"secret_to_normalizer": 42}))
    captured = []

    def respond(request):
        captured.append(request)
        # Deliberately split UTF-8 across byte chunks.
        return httpx.Response(200, headers={"Content-Type": "text/event-stream", "Set-Cookie": "credential"},
                              stream=Chunks([body[:31], body[31:117], body[117:]]))

    model = OpenAIChatModel(model="m", api_key="never-record-this", base_url="https://example.test/v1?api_key=credential",
                            sink=bus, transport=httpx.MockTransport(respond))
    try:
        response = list(model.stream(system="system", messages=[Message("user", "任务")], tools=[]))[-1]
    finally:
        model.close()
        recorder.close()
    records = list(read_trace(path))
    request = next(r["data"] for r in records if r["type"] == "api_request")
    assert base64.b64decode(request["body_base64"]) == captured[0].content
    assert json.loads(captured[0].content)["messages"][1]["content"] == "任务"
    assert decode_chunks(records) == body
    assert response.text == "你好" and response.reasoning == "推理"
    serialized = path.read_text()
    assert "never-record-this" not in serialized and '"set-cookie", "credential"' not in serialized
    assert all(r["run_id"] == bus.run_id for r in records)
    assert all(r["data"].get("request_id") == "request" for r in records)
    assert "unknown_vendor" in serialized


def test_non_2xx_body_is_preserved_and_sdk_does_not_retry(tmp_path):
    recorder = TraceRecorder(tmp_path / "trace.jsonl", "s")
    bus = EventBus(session_id="s", recorder=recorder)
    captured = []
    payload = b'{"error":{"message":"boom"},"vendor_diagnostic":123}'

    def respond(request):
        captured.append(request)
        return httpx.Response(400, content=payload, headers={"Content-Type": "application/json"})

    model = OpenAIChatModel(model="m", api_key="k", base_url="https://example.test", sink=bus,
                            transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ModelError, match="Model request failed"):
            list(model.stream(system="s", messages=[], tools=[]))
    finally:
        model.close()
        recorder.close()
    records = list(read_trace(tmp_path / "trace.jsonl"))
    assert len(captured) == 1
    assert decode_chunks(records) == payload
    assert any(r["type"] == "api_response_start" and r["data"]["status_code"] == 400 for r in records)


@pytest.mark.parametrize("finish", [None, "length", "content_filter"])
def test_incomplete_stream_is_never_a_complete_response(finish):
    model = OpenAIChatModel(model="m", api_key="k", base_url="https://example.test",
                            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=sse(frame({"content": "partial"}, finish)),
                                                           headers={"Content-Type": "text/event-stream"})))
    try:
        with pytest.raises(ModelError, match="Incomplete"):
            list(model.stream(system="s", messages=[], tools=[]))
    finally:
        model.close()


def test_required_trace_failure_prevents_http_side_effect():
    class Broken:
        def emit(self, event):
            raise PersistenceError("disk full")
    captured = []
    bus = EventBus(session_id="s", recorder=Broken())
    model = OpenAIChatModel(model="m", api_key="k", base_url="https://example.test", sink=bus,
                            transport=httpx.MockTransport(lambda r: captured.append(r)))
    try:
        with pytest.raises(PersistenceError, match="disk full"):
            list(model.stream(system="s", messages=[], tools=[]))
    finally:
        model.close()
    assert captured == []


def test_corrupt_trace_is_not_silently_overwritten(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text('{"partial":')
    with pytest.raises(PersistenceError, match="invalid trace"):
        TraceRecorder(path, "s")
    assert path.read_text() == '{"partial":'


def test_midstream_disconnect_is_a_model_error_with_partial_raw_trace(tmp_path):
    payload = sse(frame({"content": "partial"}))[:-len(b"data: [DONE]\n\n")]
    class Disconnect(httpx.SyncByteStream):
        def __iter__(self):
            yield payload
            raise httpx.ReadError("disconnected")
    recorder = TraceRecorder(tmp_path / "trace.jsonl", "s")
    bus = EventBus(session_id="s", recorder=recorder)
    model = OpenAIChatModel(model="m", api_key="k", base_url="https://example.test", sink=bus,
                            transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Disconnect(), headers={"Content-Type": "text/event-stream"})))
    try:
        with pytest.raises(ModelError, match="disconnected"):
            list(model.stream(system="s", messages=[], tools=[]))
    finally:
        model.close()
        recorder.close()
    records = list(read_trace(tmp_path / "trace.jsonl"))
    assert decode_chunks(records) == payload
    assert next(r for r in records if r["type"] == "api_response_end")["data"]["status"] == "error"
