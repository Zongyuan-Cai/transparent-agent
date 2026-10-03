"""Offline Web UI demo: local fake API, isolated state, no real credentials.

Run from a development checkout: python scripts/web_preview.py --port 8766
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transparent_agent.config import Config
from transparent_agent.runtime import RuntimeOptions
from transparent_agent.web.server import WebServer
from transparent_agent.web.worker import SessionManager


def chunk(delta, finish=None):
    return {"id": "chatcmpl-local-demo", "object": "chat.completion.chunk", "created": 1,
            "model": "local-demo", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class DemoAPI(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(request)
        messages = request["messages"]
        user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            self.write_chunk(chunk({"reasoning_content": "先读取工作区中的文件，再根据工具结果作答。这里的推理文本由本地模拟模型提供。"}))
            if user == "slow model":
                for _ in range(200):
                    time.sleep(.05)
                    self.write_chunk(chunk({"reasoning_content": "."}))
            if messages[-1]["role"] == "user":
                slow = user == "slow tool"
                name = "bash" if slow else "read"
                args = {"command": "sleep 60 & echo $! > child.pid; wait"} if slow else {"path": "README.md"}
                call = {"index": 0, "id": "call_" + uuid.uuid4().hex[:12], "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                self.write_chunk(chunk({"content": "我先检查工作区中的文件。", "tool_calls": [call]}, "tool_calls"))
            else:
                text = "已读取项目文件。这是一个本地演示响应。\n\n### 运行过程\n- 完整输入已记录到 trace。\n- 工具结果已回传模型。\n- 每个响应片段都保留了原始字节。\n\n```python\nprint('Every step, in the open.')\n```\n\n可以在右侧查看请求 body、原始响应和工具全文。"
                if user == "long response":
                    text += "完整内容。" * 3000
                if user == "unsafe html":
                    text += "\n<script>window.__unsafe = true</script><img src=x onerror=alert(1)>"
                for index in range(0, len(text), 18):
                    self.write_chunk(chunk({"content": text[index:index + 18]}))
                    time.sleep(getattr(self.server, "delay", .015))
                self.write_chunk(chunk({}, "stop"))
            self.write_chunk({"id": "chatcmpl-local-demo", "object": "chat.completion.chunk", "created": 1,
                              "model": "local-demo", "choices": [], "usage": {"prompt_tokens": 320, "completion_tokens": 95, "total_tokens": 415}})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def write_chunk(self, value):
        self.wfile.write(f"data: {json.dumps(value, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()


def start_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoAPI)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    provider = start_api()
    try:
        with tempfile.TemporaryDirectory(prefix="transparent-web-demo-") as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            (workspace / "README.md").write_text("# Transparent Agent\n\nLocal demo workspace.\n", encoding="utf-8")
            skill = workspace / ".agents" / "skills" / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: review\ndescription: 检查工作区中的代码与文档\n---\nRead files before making claims.\n", encoding="utf-8")
            config = Config(workspace, "local-demo", "demo-key", f"http://127.0.0.1:{provider.server_port}/v1")
            manager = SessionManager(config, RuntimeOptions(state_dir=Path(directory) / "state"))
            server = WebServer(manager, port=args.port)
            print(f"Offline demo: {server.url} (fake model, temporary workspace)", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
    finally:
        provider.shutdown()
        provider.server_close()


if __name__ == "__main__":
    main()
