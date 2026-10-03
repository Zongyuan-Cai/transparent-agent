"""Filesystem tools: read, write, edit, ls."""

from __future__ import annotations

import difflib
import time
from pathlib import Path

from .base import Tool, ToolContext, ToolError, ToolResult, rel_path, require_str, resolve_in_workspace

MAX_READ_LINES = 2000
MAX_READ_BYTES = 5_000_000
MAX_LS_ENTRIES = 200
MAX_DIFF_LINES = 80


def tool_read(args: dict, ctx: ToolContext) -> ToolResult:
    path = resolve_in_workspace(ctx, require_str(args, "path"))
    name = rel_path(ctx.workspace, path)
    if not path.exists():
        raise ToolError(f"file not found: {name}")
    if path.is_dir():
        raise ToolError(f"{name} is a directory; use ls to list directories")

    size = path.stat().st_size
    with open(path, "rb") as handle:
        data = handle.read(MAX_READ_BYTES + 1)
    clipped = len(data) > MAX_READ_BYTES
    data = data[:MAX_READ_BYTES]
    if b"\x00" in data[:8192]:
        raise ToolError(f"{name} looks like a binary file ({size} bytes); read only handles text")

    try:
        # A byte window can end inside a UTF-8 character; decoding the
        # complete prefix via an incremental decoder preserves valid files.
        import codecs
        lines = codecs.getincrementaldecoder("utf-8")().decode(data, final=not clipped).splitlines()
    except UnicodeDecodeError as exc:
        raise ToolError(f"{name} is not valid UTF-8 text") from exc
    if clipped and lines:
        lines.pop()  # the last line of the window may be cut in half
    available = len(lines)
    if available == 0:
        return ToolResult(f"{name} is empty (0 bytes)")

    start = _optional_int(args, "start_line")
    end = _optional_int(args, "end_line")
    if start is None and end is None:
        start, end = 1, min(available, MAX_READ_LINES)
    else:
        start = 1 if start is None else start
        if start < 1:
            raise ToolError("start_line must be >= 1")
        if start > available:
            if clipped:
                raise ToolError(
                    f"start_line {start} is beyond the part of {name} that read can access "
                    f"(the file is larger than {_megabytes(MAX_READ_BYTES)} MB); "
                    "use grep or bash to inspect it"
                )
            raise ToolError(f"start_line {start} is beyond the end of the file ({available} lines)")
        end = min(available if end is None else end, available)
        if end < start:
            raise ToolError(f"end_line {end} is before start_line {start}")

    scope = f"lines {start}-{end} of {available}" if not clipped else f"lines {start}-{end}"
    notes = []
    if clipped:
        notes.append(
            f"[the file is larger than {_megabytes(MAX_READ_BYTES)} MB; "
            f"read only accesses its first {available} lines]"
        )
    if end < available or clipped:
        notes.append("[use start_line/end_line to read further lines]")
    header = f"{name} ({scope})" + "".join(f"\n{note}" for note in notes)
    body = "\n".join(f"{number:>6}\t{line}" for number, line in enumerate(lines[start - 1:end], start))
    return ToolResult(f"{header}\n{body}")


def tool_write(args: dict, ctx: ToolContext) -> ToolResult:
    path = resolve_in_workspace(ctx, require_str(args, "path"))
    content = require_str(args, "content", allow_empty=True)
    name = rel_path(ctx.workspace, path)
    if path.exists() and path.is_dir():
        raise ToolError(f"{name} is a directory")
    existed = path.exists()
    previous = len(path.read_bytes()) if existed else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    newline_count = content.count("\n")
    lines = newline_count + (0 if content.endswith("\n") or not content else 1)
    action = f"Overwrote {name}" if existed else f"Created {name}"
    detail = f"{len(content.encode('utf-8'))} bytes, {lines} lines"
    if existed:
        detail += f" (was {previous} bytes)"
    return ToolResult(f"{action}: {detail}")


def tool_edit(args: dict, ctx: ToolContext) -> ToolResult:
    path = resolve_in_workspace(ctx, require_str(args, "path"))
    old = require_str(args, "old_string")
    new = require_str(args, "new_string", allow_empty=True)
    replace_all = bool(args.get("replace_all", False))
    name = rel_path(ctx.workspace, path)
    if not path.is_file():
        raise ToolError(f"file not found: {name}")
    if old == new:
        raise ToolError("old_string and new_string are identical; nothing to change")

    try:
        # Read bytes so CRLF line endings are preserved exactly.
        original = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"{name} is not valid UTF-8 text; edit only handles text files")

    updated = None
    matches = original.count(old)
    if matches:
        _require_unambiguous(matches, name, replace_all)
        updated = original.replace(old, new) if replace_all else original.replace(old, new, 1)
    else:
        # Fallback for files (and models) that use CRLF line endings.
        if "\r\n" in original and "\n" not in original.replace("\r\n", ""):
            normalized = original.replace("\r\n", "\n")
            matches = normalized.count(old)
            if matches:
                _require_unambiguous(matches, name, replace_all)
                replaced = normalized.replace(old, new) if replace_all else normalized.replace(old, new, 1)
                updated = replaced.replace("\n", "\r\n")

    if updated is None:
        raise ToolError(
            f"old_string not found in {name}. It must match the file exactly, including indentation. "
            "Note that read shows line numbers followed by a tab; those are not part of the content."
        )

    replacements = matches if replace_all else 1
    path.write_bytes(updated.encode("utf-8"))
    diff = list(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            lineterm="",
            n=2,
        )
    )
    diff_text = "\n".join(diff)
    if len(diff) > MAX_DIFF_LINES:
        diff_text = "\n".join(diff[:MAX_DIFF_LINES]) + "\n... [diff truncated]"
    plural = "" if replacements == 1 else "s"
    return ToolResult(f"Edited {name}: {replacements} replacement{plural}\n{diff_text}")


def tool_ls(args: dict, ctx: ToolContext) -> ToolResult:
    raw = args.get("path") or "."
    path = resolve_in_workspace(ctx, raw)
    name = rel_path(ctx.workspace, path)
    if not path.exists():
        raise ToolError(f"path not found: {name}")
    if path.is_file():
        stat = path.stat()
        return ToolResult(f"{name} is a file ({stat.st_size} bytes, modified {_mtime(stat.st_mtime)})")

    entries = sorted(path.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower()))
    note = ""
    if len(entries) > MAX_LS_ENTRIES:
        note = f"\n[showing first {MAX_LS_ENTRIES} of {len(entries)} entries; use find to search the tree]"
        entries = entries[:MAX_LS_ENTRIES]
    if not entries:
        return ToolResult(f"{name}/ is empty")
    listing = "\n".join(
        f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries
    )
    return ToolResult(f"{name}/ ({len(entries)} entries){note}\n{listing}")


def _megabytes(byte_count: int) -> int:
    return byte_count // 1_000_000


def _require_unambiguous(matches: int, name: str, replace_all: bool) -> None:
    if matches > 1 and not replace_all:
        raise ToolError(
            f"old_string appears {matches} times in {name}; add more surrounding context to make "
            "it unique, or pass replace_all=true"
        )


def _optional_int(args: dict, name: str) -> int | None:
    value = args.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolError(f"argument '{name}' must be an integer")
    return value


def _mtime(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))


TOOLS = [
    Tool(
        name="read",
        description=(
            "Read a text file from the workspace, with line numbers. Reads the whole file when it "
            "is small; use start_line/end_line for large files or targeted sections."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root"},
                "start_line": {"type": "integer", "description": "First line to read (1-based)"},
                "end_line": {"type": "integer", "description": "Last line to read (inclusive)"},
            },
            "required": ["path"],
        },
        run=tool_read,
    ),
    Tool(
        name="write",
        description=(
            "Create a new file, or replace the complete contents of an existing file. Parent "
            "directories are created. For changing an existing file, prefer edit."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["path", "content"],
        },
        run=tool_write,
    ),
    Tool(
        name="edit",
        description=(
            "Replace an exact string in an existing file. old_string must match the file exactly "
            "(including indentation) and must be unique unless replace_all is true. Include a few "
            "surrounding lines to make it unique."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root"},
                "old_string": {"type": "string", "description": "Exact text to replace"},
                "new_string": {"type": "string", "description": "Replacement text"},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every occurrence (default: only if unique)",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
        run=tool_edit,
    ),
    Tool(
        name="ls",
        description="List the entries of a directory (non-recursive). Use find to search a tree.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path relative to the workspace root (default: .)"},
            },
        },
        run=tool_ls,
    ),
]
