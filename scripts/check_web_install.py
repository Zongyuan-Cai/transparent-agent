"""Run with the installed wheel's Python, outside the source checkout."""

import tempfile
import threading
from pathlib import Path

import httpx
import transparent_agent

from transparent_agent.config import Config
from transparent_agent.runtime import RuntimeOptions
from transparent_agent.web.server import WebServer
from transparent_agent.web.worker import SessionManager


def main():
    location = Path(transparent_agent.__file__).resolve()
    assert "site-packages" in location.parts, f"expected installed package, got {location}"
    with tempfile.TemporaryDirectory(prefix="transparent-web-install-") as directory:
        root = Path(directory)
        server = WebServer(SessionManager(Config(root, "test", None, None), RuntimeOptions(state_dir=root / "state")), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=server.url, trust_env=False) as client:
                assert "Agent 工作台" in client.get("/").text
                assert "function inspectEvent" in client.get("/app.js").text
                assert "overflow:clip" in client.get("/styles.css").text
                assert client.get("/api/config").json()["configured"] is False
                assert client.get("/api/sessions").json() == {"sessions": []}
            print(f"Installed Web assets and API verified: {location}")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    main()
