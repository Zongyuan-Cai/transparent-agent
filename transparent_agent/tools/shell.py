"""The bash tool: run real shell commands from the workspace."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .base import Tool, ToolContext, ToolError, ToolResult, rel_path, require_str, resolve_in_workspace

OUTPUT_HEAD_BYTES = 1_000_000
OUTPUT_TAIL_BYTES = 1_000_000


def tool_bash(args: dict, ctx: ToolContext) -> ToolResult:
    command = require_str(args, "command")
    note = ""

    timeout = args.get("timeout_s")
    if timeout is not None:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ToolError("argument 'timeout_s' must be a positive number of seconds")
        timeout = float(timeout)
        if timeout > ctx.max_bash_timeout:
            note += f"\n[timeout_s {timeout:g} exceeds the maximum {ctx.max_bash_timeout:g}s and was clamped]"
            timeout = ctx.max_bash_timeout
    else:
        timeout = ctx.bash_timeout

    if args.get("cwd") is not None:
        cwd = resolve_in_workspace(ctx, args["cwd"])
        if not cwd.is_dir():
            raise ToolError(f"cwd not found or not a directory: {args['cwd']}")
    else:
        cwd = ctx.workspace

    started = time.monotonic()
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="ta-bash-") as tmp:
        stdout_path = Path(tmp) / "stdout"
        stderr_path = Path(tmp) / "stderr"
        with open(stdout_path, "wb") as stdout_file, open(stderr_path, "wb") as stderr_file:
            try:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=str(cwd),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,  # own process group, so we can kill the whole tree
                )
            except OSError as exc:
                raise ToolError(f"failed to start command: {exc}")
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = _terminate(process)
        except BaseException:
            # KeyboardInterrupt (user cancellation) or anything else: never leave
            # the command's process group running.
            _terminate(process)
            raise
        duration = time.monotonic() - started
        stdout, stdout_clipped = _read_clipped(stdout_path)
        stderr, stderr_clipped = _read_clipped(stderr_path)

    status = "timed_out" if timed_out else "completed"
    lines = [
        f"status: {status}",
        f"exit_code: {_describe_exit(returncode)}",
        f"duration: {duration:.2f}s",
        f"cwd: {rel_path(ctx.workspace, cwd)}",
    ]
    if note:
        lines.append(note.strip())
    if timed_out:
        lines.append(f"[command exceeded the {timeout:g}s timeout and its process group was terminated]")
    if stdout_clipped or stderr_clipped:
        lines.append("[output was very large; only the beginning and end are shown]")
    lines.append("--- stdout ---")
    lines.append(stdout.rstrip("\n") if stdout.strip() else "(empty)")
    lines.append("--- stderr ---")
    lines.append(stderr.rstrip("\n") if stderr.strip() else "(empty)")

    display_parts = []
    if timed_out:
        display_parts.append(f"[timed out after {timeout:g}s]")
    elif returncode != 0:
        display_parts.append(f"[exit {_describe_exit(returncode)}]")
    if stdout.strip():
        display_parts.append(stdout.rstrip("\n"))
    if stderr.strip():
        display_parts.append(stderr.rstrip("\n"))
    display = "\n".join(display_parts) if display_parts else "(no output)"

    return ToolResult("\n".join(lines), display=display)


def _terminate(process: subprocess.Popen) -> int:
    """Kill the command's whole process group, then return its exit status."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    return process.returncode if process.returncode is not None else -signal.SIGKILL


def _describe_exit(returncode: int) -> str:
    if returncode >= 0:
        return str(returncode)
    try:
        name = signal.Signals(-returncode).name
    except ValueError:
        name = "unknown"
    return f"killed by signal {name}"


def _read_clipped(path: Path) -> tuple[str, bool]:
    size = path.stat().st_size
    if size <= OUTPUT_HEAD_BYTES + OUTPUT_TAIL_BYTES:
        return path.read_bytes().decode("utf-8", errors="replace"), False
    with open(path, "rb") as handle:
        head = handle.read(OUTPUT_HEAD_BYTES)
        handle.seek(size - OUTPUT_TAIL_BYTES)
        tail = handle.read(OUTPUT_TAIL_BYTES)
    text = head.decode("utf-8", errors="replace") + tail.decode("utf-8", errors="replace")
    return text, True


TOOLS = [
    Tool(
        name="bash",
        description=(
            "Run a shell command from the workspace and return stdout, stderr, exit code and "
            "duration. Use it for tests, builds, git, scripts. Non-zero exit codes are normal "
            "observations, not tool failures. Interactive commands do not work."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd": {"type": "string", "description": "Directory to run in, relative to the workspace (default: workspace root)"},
                "timeout_s": {"type": "number", "description": "Timeout in seconds (default and maximum come from configuration)"},
            },
            "required": ["command"],
        },
        run=tool_bash,
    ),
]
