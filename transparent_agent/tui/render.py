"""Renders the agent's event stream as a normal coding-agent transcript."""

from __future__ import annotations

import json
import sys
from typing import TextIO

MAX_DISPLAY_LINES = 20
MAX_DISPLAY_CHARS = 4000


class Style:
    """Minimal ANSI styling; disabled when the output is not a terminal."""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled else text

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)


class Renderer:
    """An event sink that prints. Ignores every event the user should not see."""

    def __init__(self, *, style: Style, out: TextIO | None = None, log: TextIO | None = None):
        self.style = style
        self.out = out if out is not None else sys.stdout
        self.log = log if log is not None else self.out
        self._mid_line = False

    # -- event sink --------------------------------------------------------

    def emit(self, event) -> None:
        handler = getattr(self, f"_on_{event.type}", None)
        if handler is not None:
            handler(event.data)

    # -- assistant text ----------------------------------------------------

    def _on_model_text_delta(self, data) -> None:
        if not self._mid_line:
            self.out.write(f"{self.style.cyan('●')} ")
            self._mid_line = True
        self.out.write(data["text"])
        self.out.flush()

    def _on_model_response(self, data) -> None:
        self._close_line()

    # -- tools -------------------------------------------------------------

    def _on_tool_call_start(self, data) -> None:
        self._close_line()
        summary = summarize_call(data["name"], data["arguments"])
        line = f"  {self.style.cyan('→')} {self.style.bold(data['name'])}"
        if summary:
            line += f" {self.style.dim(summary)}"
        self.log.write(line + "\n")
        self.log.flush()

    def _on_tool_call_end(self, data) -> None:
        if data["is_error"]:
            self._print_block(data["output"], self.style.red, prefix="  ")
            return
        display = data.get("display")
        if display:
            self._print_block(display, self.style.dim, prefix="  ")

    # -- lifecycle ---------------------------------------------------------

    def _on_agent_error(self, data) -> None:
        self._close_line()
        self.log.write(f"{self.style.red('✗')} {data['message']}\n")
        self.log.flush()

    def _on_agent_finish(self, data) -> None:
        self._close_line()
        reason = data["stop_reason"]
        usage = data.get("usage") or {}
        steps = data["steps"]
        if reason == "final":
            suffix = f"{steps} step{'s' if steps != 1 else ''}"
            if usage.get("total_tokens"):
                suffix += f", {format_tokens(usage['total_tokens'])} tokens"
            self.log.write(self.style.dim(f"✓ done ({suffix})") + "\n")
        elif reason == "cancelled":
            self.log.write(self.style.yellow("⚠ interrupted") + "\n")
        elif reason == "max_steps":
            self.log.write(self.style.yellow(f"⚠ {data['error']}") + "\n")
        # reason == "error" was already reported through agent_error
        self.log.flush()

    # -- helpers -----------------------------------------------------------

    def _close_line(self) -> None:
        if self._mid_line:
            self.out.write("\n")
            self.out.flush()
            self._mid_line = False

    def _print_block(self, text: str, style, prefix: str) -> None:
        lines = text.splitlines()
        if len(lines) > MAX_DISPLAY_LINES:
            half = MAX_DISPLAY_LINES // 2
            lines = lines[:half] + [f"... ({len(lines) - MAX_DISPLAY_LINES} more lines)"] + lines[-half:]
        body = "\n".join(lines)[:MAX_DISPLAY_CHARS]
        self._close_line()
        for line in body.splitlines():
            self.log.write(f"{prefix}{style(line)}\n")
        self.log.flush()


def summarize_call(name: str, raw_arguments: str, limit: int = 110) -> str:
    """A one-line summary of a tool call for display."""
    try:
        args = json.loads(raw_arguments) if raw_arguments.strip() else {}
    except json.JSONDecodeError:
        return _one_line(raw_arguments, limit)
    if not isinstance(args, dict):
        return _one_line(json.dumps(args), limit)

    if name == "bash":
        return _one_line(str(args.get("command", "")), limit)
    if name in ("read", "write", "edit", "ls"):
        text = str(args.get("path", ""))
        if name == "read" and (args.get("start_line") or args.get("end_line")):
            text += f" (lines {args.get('start_line', 1)}-{args.get('end_line', 'end')})"
        return _one_line(text, limit)
    if name == "grep":
        text = f"/{args.get('pattern', '')}/"
        if args.get("glob"):
            text += f" [{args['glob']}]"
        if args.get("path"):
            text += f" in {args['path']}"
        return _one_line(text, limit)
    if name == "find":
        text = str(args.get("pattern", ""))
        if args.get("path"):
            text += f" under {args['path']}"
        return _one_line(text, limit)
    return _one_line(json.dumps(args, ensure_ascii=False), limit)


def _one_line(text: str, limit: int) -> str:
    text = " ⏎ ".join(text.splitlines())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_tokens(count: int) -> str:
    if count < 1000:
        return str(count)
    if count < 1_000_000:
        return f"{count / 1000:.1f}k"
    return f"{count / 1_000_000:.1f}M"
