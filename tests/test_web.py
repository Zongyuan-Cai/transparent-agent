"""Web client contract against real HTTP and real child processes."""

import base64
import gzip
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from scripts.web_preview import start_api
from transparent_agent.config import Config
from transparent_agent.runtime import RuntimeOptions
from transparent_agent.sessions import SessionStore
from transparent_agent.storage import PersistenceError
from transparent_agent.web.server import WebServer
from transparent_agent.web.trace_view import TraceView
from transparent_agent.web.worker import SessionManager


@pytest.fixture
def web(tmp_path):
    (tmp_path / "README.md").write_text("# Test workspace\n", encoding="utf-8")
    skill = tmp_path / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: review\ndescription: Review files\n---\nRead first.\n", encoding="utf-8")
    provider = start_api()
    provider.delay = 0
    config = Config(tmp_path, "local-demo", "test-secret-key", f"http://127.0.0.1:{provider.server_port}/v1")
    manager = SessionManager(config, RuntimeOptions(state_dir=tmp_path / "state"))
    server = WebServer(manager, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(base_url=server.url, trust_env=False, timeout=30) as client:
        client.headers["X-Transparent-Token"] = client.get("/api/config").json()["token"]
        yield client, server, provider
    server.shutdown()
    server.server_close()
    provider.shutdown()
    provider.server_close()
    thread.join(2)


def create(client):
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def wait_until(callback, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = callback()
        if value:
            return value
        time.sleep(.03)
    pytest.fail("condition did not complete in time")


def wait_idle(client, sid):
    return wait_until(lambda: (d if (d := client.get(f"/api/sessions/{sid}").json())["status"] in ("idle", "failed") else None))


def test_web_turn_transparency_and_persistent_resume(web):
    client, server, provider = web
    sid = create(client)
    root = f"/api/sessions/{sid}"
    assert client.post(root + "/runs", json={"text": "inspect"}).status_code == 202
    detail = wait_idle(client, sid)
    assert detail["result"]["stop_reason"] == "final"
    assert any(m["role"] == "tool" for m in detail["messages"])
    assert "test-secret-key" not in client.get("/api/config").text
    records = [json.loads(line) for line in client.get(root + "/trace").text.splitlines()]
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    wire = next(r for r in records if r["type"] == "api_request")
    request = client.get(root + f'/requests/{wire["data"]["request_id"]}').json()
    assert json.loads(base64.b64decode(wire["data"]["body_base64"])) == provider.requests[0]
    assert "[DONE]" in request["response_text"]
    assert "test-secret-key" not in json.dumps(request)
    result = next(r for r in records if r["type"] == "tool_call_end")
    assert client.get(root + f'/events/{result["seq"]}').json()["data"]["full_output"] == result["data"]["full_output"]
    page = client.get(root + "/events?after=3&limit=2").json()
    assert [r["seq"] for r in page["events"]] == [4, 5]
    assert page["next"] == 5

    # Shut down the owning worker and resume through an independent server.
    server.manager.close()
    replacement = SessionManager(server.manager.config, server.manager.options)
    other = WebServer(replacement, port=0)
    thread = threading.Thread(target=other.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=other.url, trust_env=False) as resumed:
            resumed.headers["X-Transparent-Token"] = resumed.get("/api/config").json()["token"]
            assert resumed.get(root).json()["messages"] == detail["messages"]
            assert resumed.get(root).json()["resources"]["skills"][0]["name"] == "review"
            assert resumed.post(root + "/runs", json={"text": "continue"}).status_code == 202
            after = wait_idle(resumed, sid)
            assert after["result"]["stop_reason"] == "final"
            assert len(after["messages"]) > len(detail["messages"])
            assert "session_resumed" in resumed.get(root + "/trace").text
    finally:
        other.shutdown()
        other.server_close()


def test_cancel_cleans_shell_descendants_and_next_run_works(web):
    client, server, _ = web
    sid = create(client)
    root = f"/api/sessions/{sid}"
    assert client.post(root + "/runs", json={"text": "slow tool"}).status_code == 202
    pid_file = server.manager.config.workspace / "child.pid"
    wait_until(pid_file.exists)
    pid = int(pid_file.read_text())
    assert client.post(root + "/runs", json={"text": "overlap"}).status_code == 409
    assert client.post(root + "/clear", json={}).status_code == 409
    assert client.post(root + "/cancel", json={}).status_code == 202
    detail = wait_idle(client, sid)
    assert detail["result"]["stop_reason"] == "cancelled"
    assert not Path(f"/proc/{pid}").exists()
    assert client.post(root + "/runs", json={"text": "after cancel"}).status_code == 202
    assert wait_idle(client, sid)["result"]["stop_reason"] == "final"


def test_cancel_model_stream_and_clear_keeps_trace(web):
    client, _, _ = web
    sid = create(client)
    root = f"/api/sessions/{sid}"
    client.post(root + "/runs", json={"text": "slow model"})
    wait_until(lambda: "api_response_start" in client.get(root + "/trace").text)
    client.post(root + "/cancel", json={})
    assert wait_idle(client, sid)["result"]["stop_reason"] == "cancelled"
    before = client.get(root + "/trace").text
    cleared = client.post(root + "/clear", json={})
    assert cleared.status_code == 200
    assert cleared.json()["messages"] == []
    assert client.get(root + "/trace").text.startswith(before)


def test_immediate_cancel_keeps_worker_usable(web):
    client, _, _ = web
    sid = create(client)
    root = f"/api/sessions/{sid}"
    for _ in range(5):
        assert client.post(root + "/runs", json={"text": "slow model"}).status_code == 202
        assert client.post(root + "/cancel", json={}).status_code == 202
        detail = wait_idle(client, sid)
        assert detail["status"] == "idle"
        assert detail["result"]["stop_reason"] == "cancelled"


def test_idle_worker_eviction_keeps_saved_sessions(web):
    client, server, _ = web
    server.manager.max_workers = 1
    first = create(client)
    second = create(client)
    assert first not in server.manager.workers
    assert second in server.manager.workers
    assert len(client.get("/api/sessions").json()["sessions"]) == 2
    assert client.get(f"/api/sessions/{first}").json()["resources"]["skills"][0]["name"] == "review"
    assert client.post(f"/api/sessions/{first}/runs", json={"text": "inspect"}).status_code == 202
    assert wait_idle(client, first)["result"]["stop_reason"] == "final"


def test_sse_cursor_and_browser_disconnect_does_not_cancel(web):
    client, _, _ = web
    sid = create(client)
    root = f"/api/sessions/{sid}"
    client.post(root + "/runs", json={"text": "inspect"})
    with client.stream("GET", root + "/stream?after=1", headers={"Last-Event-ID": "3"}) as response:
        assert response.status_code == 200
        line = next(line for line in response.iter_lines() if line.startswith("id: "))
        assert line == "id: 4"
    assert wait_idle(client, sid)["result"]["stop_reason"] == "final"


def test_local_http_boundaries_and_input_validation(web):
    client, server, _ = web
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/config", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/sessions", json={}, headers={"X-Transparent-Token": "invalid"}).status_code == 403
    assert client.post("/api/sessions", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/sessions", content="{", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/sessions", json={"workspace": 3}).status_code == 400
    assert client.get("/api/sessions/not-an-id").status_code == 404
    assert client.get("/static/../../pyproject.toml").status_code == 404
    for asset in ("/", "/app.js", "/styles.css"):
        response = client.get(asset)
        assert response.status_code == 200
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    sid = create(client)
    assert client.post(f"/api/sessions/{sid}/runs", json={"text": " "}).status_code == 400
    assert client.get(f"/api/sessions/{sid}/events?limit=999").status_code == 400
    with pytest.raises(ValueError, match="loopback"):
        WebServer(server.manager, "0.0.0.0", 0)


def test_locked_tui_session_cannot_run_in_web(web):
    client, server, _ = web
    sid = create(client)
    worker = server.manager.workers.pop(sid)
    worker.close()
    with_store = SessionStore(server.manager.path(sid))
    try:
        response = client.post(f"/api/sessions/{sid}/runs", json={"text": "inspect"})
        assert response.status_code == 409
        assert "locked" in response.json()["error"]
    finally:
        with_store.close()


def record(seq, type, data):
    return {"schema_version": 1, "session_id": "a" * 32, "run_id": "run", "seq": seq,
            "type": type, "timestamp": "2026-10-03T00:00:00+00:00", "data": data}


def test_trace_index_partial_append_and_exact_byte_reassembly(tmp_path):
    path = tmp_path / "trace.jsonl"
    payload = gzip.compress("data: 中文\n\ndata: [DONE]\n\n".encode())
    records = [record(1, "api_response_start", {"request_id": "req", "headers": [["content-encoding", "gzip"]]}),
               record(2, "api_response_chunk", {"request_id": "req", "body_base64": base64.b64encode(payload[:7]).decode()}),
               record(3, "api_response_chunk", {"request_id": "req", "body_base64": base64.b64encode(payload[7:]).decode()})]
    lines = [json.dumps(r).encode() + b"\n" for r in records]
    path.write_bytes(lines[0] + lines[1][:15])
    view = TraceView(path, "a" * 32)
    assert view.page()["total"] == 1
    with path.open("ab") as handle:
        handle.write(lines[1][15:] + lines[2])
    assert view.page()["total"] == 3
    request = view.request("req")
    assert base64.b64decode(request["response_body_base64"]) == payload
    assert request["response_text"] == "data: 中文\n\ndata: [DONE]\n\n"
    with path.open("ab") as handle:
        handle.write(json.dumps(record(9, "bad", {})).encode() + b"\n")
    with pytest.raises(PersistenceError, match="sequence"):
        view.refresh()


def test_web_import_has_no_tui_dependency():
    import subprocess
    result = subprocess.run([sys.executable, "-c", "import sys; import transparent_agent.web.server; assert 'transparent_agent.tui' not in sys.modules; assert 'transparent_agent.cli' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_long_model_output_summary_does_not_truncate_original(web):
    client, _, _ = web
    sid = create(client)
    root = f"/api/sessions/{sid}"
    client.post(root + "/runs", json={"text": "long response"})
    detail = wait_idle(client, sid)
    full = detail["messages"][-1]["content"]
    assert len(full) > 8192
    records = [json.loads(line) for line in client.get(root + "/trace").text.splitlines()]
    response = next(r for r in reversed(records) if r["type"] == "model_response")
    page = client.get(root + f'/events?after={response["seq"] - 1}&limit=1').json()
    assert len(page["events"][0]["data"]["text"]) == 8192
    assert client.get(root + f'/events/{response["seq"]}').json()["data"]["text"] == full
