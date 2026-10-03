"""Search tools: find (paths) and grep (content).

grep prefers ripgrep when it is installed and falls back to a pure-Python
walk, which keeps the tool usable on any machine.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .base import Tool, ToolContext, ToolError, ToolResult, rel_path, require_str, resolve_in_workspace

MAX_FIND_RESULTS = 200
MAX_GREP_MATCHES = 200
MAX_LINE_CHARS = 300
MAX_SCANNED_FILE_BYTES = 2_000_000
SEARCH_DEADLINE_S = 30.0

# Directories that are never worth searching by default.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build",
    ".idea", ".vscode",
}


def tool_find(args: dict, ctx: ToolContext) -> ToolResult:
    pattern = require_str(args, "pattern")
    kind = args.get("type") or "any"
    if kind not in ("any", "file", "dir"):
        raise ToolError("argument 'type' must be one of: any, file, dir")
    root = resolve_in_workspace(ctx, args.get("path") or ".")
    if not root.exists():
        raise ToolError(f"path not found: {rel_path(ctx.workspace, root)}")
    if root.is_file():
        raise ToolError(f"{rel_path(ctx.workspace, root)} is a file; pass a directory as 'path'")

    matches: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        candidates: list[tuple[str, bool]] = [(d, True) for d in dirnames]
        candidates += [(f, False) for f in sorted(filenames)]
        for name, is_dir in candidates:
            if kind == "file" and is_dir:
                continue
            if kind == "dir" and not is_dir:
                continue
            full = Path(dirpath) / name
            if not _find_matches(pattern, name, full, root):
                continue
            label = rel_path(ctx.workspace, full)
            matches.append(label + "/" if is_dir else label)
            if len(matches) >= MAX_FIND_RESULTS:
                truncated = True
                break
        if truncated:
            break

    scope = rel_path(ctx.workspace, root)
    if not matches:
        return ToolResult(f"no files or directories matching {pattern!r} under {scope}")
    matches.sort()
    text = "\n".join(matches)
    if truncated:
        text += f"\n[stopped at {MAX_FIND_RESULTS} matches; narrow the pattern or the path]"
    return ToolResult(f"{len(matches)} match(es) for {pattern!r} under {scope}:\n{text}")


def tool_grep(args: dict, ctx: ToolContext) -> ToolResult:
    pattern = require_str(args, "pattern")
    glob = args.get("glob") or None
    if glob is not None and not isinstance(glob, str):
        raise ToolError("argument 'glob' must be a string like '*.py'")
    ignore_case = bool(args.get("ignore_case", False))
    root = resolve_in_workspace(ctx, args.get("path") or ".")
    if not root.exists():
        raise ToolError(f"path not found: {rel_path(ctx.workspace, root)}")

    try:
        re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"invalid regular expression {pattern!r}: {exc}")

    if shutil.which("rg"):
        matches, note = _grep_ripgrep(pattern, root, glob, ignore_case, ctx)
    else:
        matches, note = _grep_python(pattern, root, glob, ignore_case, ctx)

    scope = rel_path(ctx.workspace, root)
    if not matches:
        return ToolResult(f"no matches for {pattern!r} in {scope}" + note, metadata={"backend": "rg" if shutil.which("rg") else "python"})
    header = f"{len(matches)} match(es) for {pattern!r} in {scope}:"
    return ToolResult(header + note + "\n" + "\n".join(matches), metadata={"backend": "rg" if shutil.which("rg") else "python"})


def _find_matches(pattern: str, name: str, full: Path, root: Path) -> bool:
    if "/" in pattern:
        relative = full.relative_to(root).as_posix()
        return fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(relative, f"**/{pattern}")
    return fnmatch.fnmatchcase(name, pattern)


def _grep_ripgrep(pattern: str, root: Path, glob: str | None, ignore_case: bool,
                  ctx: ToolContext) -> tuple[list[str], str]:
    command = [
        "rg", "--json", "--hidden", "--max-columns", str(MAX_LINE_CHARS),
        "--max-columns-preview", "-g", "!.git",
    ]
    if ignore_case:
        command.append("-i")
    if glob:
        command += ["-g", glob]
    command += ["-e", pattern, "--", str(root)]

    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        raise ToolError(f"failed to run ripgrep: {exc}")

    matches: list[str] = []
    truncated = False
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") != "match":
                continue
            data = record["data"]
            path_text = data["path"].get("text")
            if path_text is None:
                continue
            line_number = data.get("line_number")
            line_text = (data.get("lines") or {}).get("text")
            if line_number is None or line_text is None:
                continue
            label = _display_path(ctx.workspace, path_text)
            matches.append(f"{label}:{line_number}:{line_text.rstrip(chr(10))[:MAX_LINE_CHARS]}")
            if len(matches) >= MAX_GREP_MATCHES:
                truncated = True
                process.terminate()
                break
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    finally:
        if process.poll() is None:
            process.kill()

    stderr = process.stderr.read() if process.stderr else ""
    if process.returncode not in (0, 1) and not matches and not truncated:
        raise ToolError(f"ripgrep failed: {stderr.strip() or f'exit code {process.returncode}'}")
    note = f"\n[more matches exist; output limited to the first {MAX_GREP_MATCHES}]" if truncated else ""
    return matches, note


def _grep_python(pattern: str, root: Path, glob: str | None, ignore_case: bool,
                 ctx: ToolContext) -> tuple[list[str], str]:
    flags = re.IGNORECASE if ignore_case else 0
    regex = re.compile(pattern, flags)
    deadline = time.monotonic() + SEARCH_DEADLINE_S
    matches: list[str] = []
    limited = False
    timed_out = False

    files: list[Path] = []
    if root.is_file():
        files = [root]
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for filename in sorted(filenames):
                if glob and not fnmatch.fnmatchcase(filename, glob):
                    continue
                files.append(Path(dirpath) / filename)

    for path in files:
        if time.monotonic() > deadline:
            timed_out = True
            break
        try:
            if not path.resolve().is_relative_to(ctx.workspace.resolve()):
                continue
            if path.stat().st_size > MAX_SCANNED_FILE_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue
        label = _display_path(ctx.workspace, str(path))
        try:
            lines = data.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, 1):
            if regex.search(line):
                matches.append(f"{label}:{number}:{line[:MAX_LINE_CHARS]}")
                if len(matches) >= MAX_GREP_MATCHES:
                    limited = True
                    break
        if limited:
            break

    note = ""
    if limited:
        note = f"\n[more matches exist; output limited to the first {MAX_GREP_MATCHES}]"
    elif timed_out:
        note = f"\n[search stopped after {SEARCH_DEADLINE_S:.0f}s; narrow the path or pattern]"
    return matches, note


def _display_path(workspace: Path, raw: str) -> str:
    path = Path(raw)
    return rel_path(workspace, path.resolve() if path.is_absolute() else path)


TOOLS = [
    Tool(
        name="find",
        description=(
            "Find files or directories by name pattern (e.g. '*.py', 'test_*', 'src/**/*.toml'), "
            "searching recursively from a path."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern for file/directory names"},
                "path": {"type": "string", "description": "Directory to search under (default: .)"},
                "type": {"type": "string", "enum": ["any", "file", "dir"], "description": "Restrict to files or directories"},
            },
            "required": ["pattern"],
        },
        run=tool_find,
    ),
    Tool(
        name="grep",
        description=(
            "Search file contents with a regular expression. Returns matching lines as "
            "path:line_number:text. Useful for finding symbols, imports, strings and tests."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression to search for"},
                "path": {"type": "string", "description": "File or directory to search (default: .)"},
                "glob": {"type": "string", "description": "Only search files matching this glob, e.g. '*.py'"},
                "ignore_case": {"type": "boolean", "description": "Case-insensitive search"},
            },
            "required": ["pattern"],
        },
        run=tool_grep,
    ),
]
