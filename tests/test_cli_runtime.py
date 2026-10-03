"""Two real CLI processes against a local HTTP endpoint; no external API."""

import base64
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from test_trace import frame, sse
from transparent_agent.trace import read_trace


def test_cli_trace_and_resume_across_processes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    (workspace / "AGENTS.md").write_text("INTEGRATION_PROJECT_RULE")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(body)
            if len(requests) == 1:
                payload = sse(frame({"tool_calls": [
                    {"index": 0, "id": "write", "function": {"name": "write", "arguments": '{"path":"result.txt","content":"verified"}'}},
                    {"index": 1, "id": "shell", "function": {"name": "bash", "arguments": '{"command":"echo checked"}'}},
                ]}, "tool_calls"))
            else:
                payload = sse(frame({"content": "continued" if len(requests) == 3 else "done"}, "stop"))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, OPENAI_API_KEY="offline-test-key", OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
               OPENAI_MODEL="local", MAX_STEPS="4", BASH_TIMEOUT_S="2", MAX_BASH_TIMEOUT_S="3",
               MODEL_TIMEOUT_S="3", MAX_TOOL_OUTPUT_CHARS="500", STREAM_USAGE="false")
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", "transparent_agent"]
    try:
        first = subprocess.run([*command, "-C", str(workspace), "--state-dir", str(tmp_path / "state"), "-p", "verify"],
                               cwd=root, env=env, text=True, capture_output=True, timeout=15)
        assert first.returncode == 0, first.stderr
        assert "done" in first.stdout and "→ write" not in first.stdout
        assert "→ write" in first.stderr and "trace:" in first.stderr
        session = next((tmp_path / "state/sessions").glob("*/session.json"))
        trace = session.with_name("trace.jsonl")
        second = subprocess.run([*command, "--resume", str(session), "-p", "continue"],
                                cwd=root, env=env, text=True, capture_output=True, timeout=15)
        assert second.returncode == 0, second.stderr
        assert "continued" in second.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert (workspace / "result.txt").read_text() == "verified"
    assert len(requests) == 3
    assert "INTEGRATION_PROJECT_RULE" in json.loads(requests[0])["messages"][0]["content"]
    assert any(m.get("tool_call_id") == "write" for m in json.loads(requests[2])["messages"])
    records = list(read_trace(trace))
    recorded_requests = [base64.b64decode(r["data"]["body_base64"]) for r in records if r["type"] == "api_request"]
    assert recorded_requests == requests
    assert "offline-test-key" not in trace.read_text()
    assert len({r["run_id"] for r in records if r["type"] == "agent_start"}) == 2
    assert any(r["type"] == "session_resumed" for r in records)
