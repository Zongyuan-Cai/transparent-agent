"""Web server entrypoint, intentionally independent of cli.py and tui.py."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from ..config import Config, ConfigError, load_dotenv
from ..runtime import RuntimeOptions
from .server import WebServer
from .worker import SessionManager, WebError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent local Web UI for Transparent Agent")
    parser.add_argument("-C", "--workspace")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--skill-dir", type=Path, action="append", default=[])
    parser.add_argument("--prompt-dir", type=Path, action="append", default=[])
    parser.add_argument("--extension", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    load_dotenv()
    try:
        config = Config.from_env(workspace=args.workspace, model=args.model, base_url=args.base_url, max_steps=args.max_steps)
        options = RuntimeOptions(state_dir=args.state_dir, skill_dirs=args.skill_dir,
                                 prompt_dirs=args.prompt_dir, extensions=args.extension)
        manager = SessionManager(config, options)
        server = WebServer(manager, args.host, args.port)
    except (ConfigError, WebError, OSError, ValueError) as exc:
        print(f"transparent-agent-web: {exc}", file=sys.stderr)
        return 1
    print(f"Transparent Agent Web: {server.url}\nWorkspace: {config.workspace}", flush=True)
    if not config.api_key:
        print("No API key configured. Set OPENAI_API_KEY in the server environment or .env before creating sessions.", flush=True)

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        # Suppress repeat interrupts while child processes clean up shell groups.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        server.server_close()
    return 0
