import pytest

from transparent_agent.tools import Tool, ToolRegistry, build_registry
from transparent_agent.tools.base import ToolError
from transparent_agent.tools.fs import MAX_LS_ENTRIES, MAX_READ_LINES, tool_edit, tool_ls, tool_read, tool_write


# -- read ------------------------------------------------------------------

def test_read_whole_file_with_line_numbers(workspace, ctx):
    (workspace / "a.py").write_text("first\nsecond\nthird\n")
    result = tool_read({"path": "a.py"}, ctx)
    assert not result.is_error
    assert "a.py (lines 1-3 of 3)" in result.output
    assert "     1\tfirst" in result.output
    assert "     3\tthird" in result.output


def test_read_line_range(workspace, ctx):
    (workspace / "a.py").write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
    result = tool_read({"path": "a.py", "start_line": 3, "end_line": 5}, ctx)
    assert "lines 3-5 of 10" in result.output
    assert "     3\tline3" in result.output
    assert "line6" not in result.output


def test_read_truncates_large_files(workspace, ctx):
    (workspace / "big.txt").write_text("\n".join(f"l{i}" for i in range(MAX_READ_LINES + 50)))
    result = tool_read({"path": "big.txt"}, ctx)
    assert f"lines 1-{MAX_READ_LINES}" in result.output
    assert "use start_line/end_line" in result.output
    assert f"l{MAX_READ_LINES + 49}" not in result.output

    tail = tool_read({"path": "big.txt", "start_line": MAX_READ_LINES + 10}, ctx)
    assert f"l{MAX_READ_LINES + 49}" in tail.output


def test_read_clips_huge_files(workspace, ctx, monkeypatch):
    from transparent_agent.tools import fs

    monkeypatch.setattr(fs, "MAX_READ_BYTES", 1000)
    (workspace / "huge.txt").write_text("\n".join(f"l{index:04d}" for index in range(500)))

    result = tool_read({"path": "huge.txt"}, ctx)
    assert "larger than" in result.output
    assert "read only accesses its first" in result.output
    with pytest.raises(ToolError, match="beyond the part of"):
        tool_read({"path": "huge.txt", "start_line": 400}, ctx)


def test_read_missing_file(workspace, ctx):
    with pytest.raises(ToolError, match="not found"):
        tool_read({"path": "nope.py"}, ctx)


def test_read_directory(workspace, ctx):
    (workspace / "src").mkdir()
    with pytest.raises(ToolError, match="is a directory"):
        tool_read({"path": "src"}, ctx)


def test_read_binary_file(workspace, ctx):
    (workspace / "blob.bin").write_bytes(b"\x00\x01\x02\xff" * 100)
    with pytest.raises(ToolError, match="binary"):
        tool_read({"path": "blob.bin"}, ctx)


def test_read_empty_file(workspace, ctx):
    (workspace / "empty.txt").write_text("")
    assert "is empty" in tool_read({"path": "empty.txt"}, ctx).output


def test_read_range_beyond_end(workspace, ctx):
    (workspace / "a.txt").write_text("one\ntwo\n")
    with pytest.raises(ToolError, match="beyond the end"):
        tool_read({"path": "a.txt", "start_line": 9}, ctx)


# -- write -----------------------------------------------------------------

def test_write_creates_file_and_parents(workspace, ctx):
    result = tool_write({"path": "pkg/sub/mod.py", "content": "x = 1\n"}, ctx)
    assert not result.is_error
    assert "Created pkg/sub/mod.py" in result.output
    assert (workspace / "pkg/sub/mod.py").read_text() == "x = 1\n"


def test_write_overwrites_and_reports(workspace, ctx):
    (workspace / "a.txt").write_text("old content")
    result = tool_write({"path": "a.txt", "content": "new"}, ctx)
    assert "Overwrote a.txt" in result.output
    assert "was 11 bytes" in result.output
    assert (workspace / "a.txt").read_text() == "new"


def test_write_allows_empty_content(workspace, ctx):
    tool_write({"path": "empty.txt", "content": ""}, ctx)
    assert (workspace / "empty.txt").read_text() == ""


def test_write_requires_string_content(workspace, ctx):
    with pytest.raises(ToolError, match="missing required string argument 'content'"):
        tool_write({"path": "a.txt", "content": 5}, ctx)


# -- edit ------------------------------------------------------------------

def test_edit_replaces_unique_string(workspace, ctx):
    (workspace / "a.py").write_text("def f():\n    return 1\n")
    result = tool_edit({"path": "a.py", "old_string": "return 1", "new_string": "return 2"}, ctx)
    assert "1 replacement" in result.output
    assert "-    return 1" in result.output
    assert "+    return 2" in result.output
    assert (workspace / "a.py").read_text() == "def f():\n    return 2\n"


def test_edit_rejects_ambiguous_match(workspace, ctx):
    (workspace / "a.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(ToolError, match="appears 2 times"):
        tool_edit({"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, ctx)


def test_edit_replace_all(workspace, ctx):
    (workspace / "a.py").write_text("x = 1\nx = 1\n")
    result = tool_edit(
        {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": True}, ctx
    )
    assert "2 replacements" in result.output
    assert (workspace / "a.py").read_text() == "x = 2\nx = 2\n"


def test_edit_reports_missing_string_with_hint(workspace, ctx):
    (workspace / "a.py").write_text("x = 1\n")
    with pytest.raises(ToolError) as excinfo:
        tool_edit({"path": "a.py", "old_string": "y = 9", "new_string": "y = 8"}, ctx)
    assert "not found" in str(excinfo.value)
    assert "read shows line numbers" in str(excinfo.value)


def test_edit_rejects_identical_strings(workspace, ctx):
    (workspace / "a.py").write_text("x = 1\n")
    with pytest.raises(ToolError, match="identical"):
        tool_edit({"path": "a.py", "old_string": "x = 1", "new_string": "x = 1"}, ctx)


def test_edit_missing_file(workspace, ctx):
    with pytest.raises(ToolError, match="not found"):
        tool_edit({"path": "nope.py", "old_string": "a", "new_string": "b"}, ctx)


def test_edit_handles_crlf_files(workspace, ctx):
    path = workspace / "win.py"
    path.write_bytes(b"def f():\r\n    return 1\r\n")
    result = tool_edit({"path": "win.py", "old_string": "    return 1", "new_string": "    return 3"}, ctx)
    assert not result.is_error
    assert path.read_bytes() == b"def f():\r\n    return 3\r\n"


def test_edit_can_delete_text(workspace, ctx):
    (workspace / "a.py").write_text("keep\ndrop\n")
    tool_edit({"path": "a.py", "old_string": "drop\n", "new_string": ""}, ctx)
    assert (workspace / "a.py").read_text() == "keep\n"


# -- ls --------------------------------------------------------------------

def test_ls_lists_entries_and_marks_dirs(workspace, ctx):
    (workspace / "src").mkdir()
    (workspace / "README.md").write_text("hi")
    result = tool_ls({"path": "."}, ctx)
    lines = result.output.splitlines()
    assert lines[0] == "./ (2 entries)"
    assert "README.md" in lines
    assert "src/" in lines


def test_ls_empty_directory(workspace, ctx):
    (workspace / "empty").mkdir()
    assert "is empty" in tool_ls({"path": "empty"}, ctx).output


def test_ls_missing_path(workspace, ctx):
    with pytest.raises(ToolError, match="not found"):
        tool_ls({"path": "missing"}, ctx)


def test_ls_file_shows_file_info(workspace, ctx):
    (workspace / "a.txt").write_text("12345")
    output = tool_ls({"path": "a.txt"}, ctx).output
    assert "is a file (5 bytes" in output


def test_ls_truncates_huge_directories(workspace, ctx):
    for index in range(MAX_LS_ENTRIES + 10):
        (workspace / f"f{index:04d}.txt").write_text("x")
    output = tool_ls({"path": "."}, ctx).output
    assert f"showing first {MAX_LS_ENTRIES}" in output


# -- registry integration --------------------------------------------------

def test_registry_turns_expected_failures_into_error_results(workspace, ctx):
    registry = build_registry()
    result = registry.execute("read", '{"path": "missing.py"}', ctx)
    assert result.is_error
    assert "not found" in result.output


def test_registry_rejects_unknown_tool(workspace, ctx):
    result = build_registry().execute("teleport", "{}", ctx)
    assert result.is_error
    assert "unknown tool" in result.output


def test_registry_reports_invalid_json(workspace, ctx):
    result = build_registry().execute("read", "{not json", ctx)
    assert result.is_error
    assert "not valid JSON" in result.output


def test_registry_accepts_empty_arguments(workspace, ctx):
    assert not build_registry().execute("ls", "", ctx).is_error


def test_registry_truncates_large_output(workspace, ctx):
    (workspace / "big.txt").write_text("x" * 5000)
    ctx.max_output_chars = 1000
    result = build_registry().execute("read", '{"path": "big.txt"}', ctx)
    assert "characters omitted" in result.output
    assert len(result.output) < 1300


def test_registry_survives_tool_crash(workspace, ctx):
    def explode(arguments, context):
        raise RuntimeError("boom")

    registry = ToolRegistry([Tool(name="boom", description="", parameters={}, run=explode)])
    result = registry.execute("boom", "{}", ctx)
    assert result.is_error
    assert "crashed: RuntimeError: boom" in result.output
