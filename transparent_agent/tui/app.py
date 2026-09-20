"""The interactive terminal loop: input, commands, banner."""

from __future__ import annotations

import sys
from typing import TextIO

from .. import __version__
from ..agent import Agent
from .render import Renderer, Style, format_tokens

HELP_TEXT = """\
Commands:
  /help          show this help
  /clear         forget the conversation and start fresh
  /status        workspace, model, conversation and token usage
  /model [NAME]  show or change the model
  /exit          quit (Ctrl+D also quits)
Input: Enter sends; end a line with \\ to continue on the next line; Ctrl+C interrupts."""


class Tui:
    def __init__(self, agent: Agent, renderer: Renderer, style: Style, *, out: TextIO | None = None):
        self.agent = agent
        self.renderer = renderer
        self.style = style
        self.out = out if out is not None else sys.stdout

    def run(self) -> int:
        self._banner()
        while True:
            text = self._read_input()
            if text is None:  # Ctrl+D / end of input
                self._write("\n")
                return 0
            if not text.strip():
                continue
            if text.startswith("/"):
                try:
                    if self._handle_command(text):
                        return 0
                except KeyboardInterrupt:
                    self._write("\n")
                continue
            try:
                self.agent.send(text)
            except KeyboardInterrupt:  # interrupt that escaped the agent loop
                self._write("\n" + self.style.yellow("⚠ interrupted") + "\n")
            self._write("\n")

    # -- input -------------------------------------------------------------

    def _read_input(self) -> str | None:
        """Read one message. None means end of input; "" means the line was aborted."""
        try:
            first = self._read_line("> ")
        except KeyboardInterrupt:
            self._write("\n")
            return ""
        if first is None:
            return None
        lines = [first]
        while lines[-1].endswith("\\"):
            try:
                following = self._read_line("… ")
            except KeyboardInterrupt:
                self._write("\n")
                return ""
            if following is None:
                break
            lines.append(following)
        return "\n".join(line[:-1].rstrip() if line.endswith("\\") else line for line in lines)

    def _read_line(self, prompt: str) -> str | None:
        if sys.stdin.isatty():
            self._write(prompt)
        try:
            return input()
        except EOFError:
            return None

    # -- commands ----------------------------------------------------------

    def _handle_command(self, text: str) -> bool:
        parts = text.split()
        command, arguments = parts[0], parts[1:]
        if command in ("/exit", "/quit"):
            return True
        if command == "/help":
            self._write(HELP_TEXT + "\n")
        elif command == "/clear":
            self.agent.clear()
            self._write(self.style.dim("conversation cleared") + "\n")
        elif command == "/status":
            self._status()
        elif command == "/model":
            if arguments:
                self.agent.model.name = arguments[0]
            self._write(f"model: {self.agent.model.name}\n")
        else:
            self._write(f"unknown command {command!r}; type /help\n")
        return False

    def _status(self) -> None:
        counts: dict[str, int] = {}
        for message in self.agent.history:
            counts[message.role] = counts.get(message.role, 0) + 1
        usage = self.agent.total_usage
        base_url = getattr(self.agent.model, "base_url", None)
        self._write(f"workspace: {self.agent.tool_context.workspace}\n")
        self._write(f"model: {self.agent.model.name}" + (f"  ({base_url})" if base_url else "") + "\n")
        breakdown = ", ".join(f"{role} {count}" for role, count in sorted(counts.items()))
        self._write(f"messages: {len(self.agent.history)}" + (f" ({breakdown})" if breakdown else "") + "\n")
        self._write(
            f"tokens: {format_tokens(usage.prompt_tokens)} in / "
            f"{format_tokens(usage.completion_tokens)} out\n"
        )
        self._write(f"max steps per turn: {self.agent.max_steps}\n")
        self._write(f"tools: {', '.join(self.agent.tools.names())}\n")

    # -- output ------------------------------------------------------------

    def _banner(self) -> None:
        base_url = getattr(self.agent.model, "base_url", None)
        self._write(f"Transparent Agent {__version__}\n")
        self._write(f"workspace: {self.agent.tool_context.workspace}\n")
        self._write(f"model: {self.agent.model.name}" + (f"  ({base_url})" if base_url else "") + "\n")
        self._write(f"tools: {', '.join(self.agent.tools.names())}\n")
        self._write(self.style.dim("type /help for commands, Ctrl+C interrupts, Ctrl+D exits") + "\n\n")

    def _write(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()
