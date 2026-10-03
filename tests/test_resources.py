import json

import pytest

from helpers import FakeModel, RecordingSink, call, text_turn, tool_turn
from test_runtime import make_skill, setup
from transparent_agent.extensions import ExtensionError
from transparent_agent.resources import Resources, build_context
from transparent_agent.runtime import Runtime, RuntimeOptions
from transparent_agent.tools import ToolError
from transparent_agent.trace import read_trace


def test_precedence_diagnostics_and_yaml_multiline(tmp_path):
    workspace, _, _ = setup(tmp_path)
    sink = RecordingSink()
    first = make_skill(tmp_path / "explicit")
    make_skill(workspace / ".agents/skills")
    bad = workspace / ".agents/skills/bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: bad\ndescription: [\n---\n")
    block = make_skill(workspace / ".agents/skills", "block")
    (block / "SKILL.md").write_text("---\nname: block\ndescription: >\n  First line\n  second line\n---\nbody")
    resources = Resources(workspace=workspace, sink=sink, skill_dirs=[tmp_path / "explicit"], home=tmp_path / "home")
    assert resources.skills["review-code"].path == first / "SKILL.md"
    assert "First line second line" in resources.skills["block"].description
    assert "bad" not in resources.skills
    assert len(sink.of_type("resource_diagnostic")) == 2


def test_explicit_only_skill_and_external_relative_resource(tmp_path):
    workspace, _, _ = setup(tmp_path)
    root = tmp_path / "external"
    skill = make_skill(root, disabled=True)
    (skill / "references").mkdir()
    (skill / "references/check.md").write_text("RESOURCE_TEXT")
    resources = Resources(workspace=workspace, sink=RecordingSink(), skill_dirs=[root], home=tmp_path / "home")
    assert "review-code" not in resources.instructions()
    with pytest.raises(ToolError, match="explicit"):
        resources.load_skill("review-code")
    assert "BODY_ONLY_AFTER_LOADING" in resources.expand("/skill:review-code inspect")
    assert "RESOURCE_TEXT" in resources.load_skill("review-code", resource="references/check.md", explicit=True).output
    with pytest.raises(ToolError, match="relative"):
        resources.load_skill("review-code", resource="../outside", explicit=True)
    (skill / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(ToolError, match="escapes"):
        resources.load_skill("review-code", resource="link", explicit=True)


def test_prompt_expansion_and_nested_project_instructions(tmp_path):
    workspace, _, _ = setup(tmp_path)
    sub = workspace / "sub"
    sub.mkdir()
    (workspace / "AGENTS.md").write_text("ROOT_RULE")
    (sub / "AGENTS.md").write_text("SUB_RULE")
    prompts = sub / ".agents/prompts"
    prompts.mkdir(parents=True)
    (prompts / "review.md").write_text("Inspect $ARGUMENTS carefully.")
    sink = RecordingSink()
    resources = Resources(workspace=sub, sink=sink, home=tmp_path / "home")
    context = build_context(sub, "base", resources)
    assert context.index("ROOT_RULE") < context.index("SUB_RULE")
    expanded = resources.expand("/prompt:review\t中文 $(does-not-execute)")
    assert expanded == "Inspect 中文 $(does-not-execute) carefully."
    assert sink.of_type("prompt_expanded")[0].data["content"] == expanded


def test_extension_tool_uses_normal_executor_and_trace(tmp_path):
    workspace, config, options = setup(tmp_path)
    extension = workspace / "extension.py"
    extension.write_text(
        "from transparent_agent.tools import Tool, ToolResult\n"
        "def register(api):\n"
        "    api.register_tool(Tool('extra', 'Example tool', {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']}, lambda args, ctx: ToolResult(str(args['value'] * 2))))\n")
    options.extensions = [extension]
    model = FakeModel([tool_turn("", call("good", "extra", '{"value":4}'), call("bad", "extra", '{"value":true}')), text_turn("ok")])
    with Runtime(config, options=options, model=model, home=tmp_path / "home") as runtime:
        assert runtime.agent.send("go").stop_reason == "final"
        trace = runtime.trace_path
        results = [m for m in runtime.agent.history if m.role == "tool"]
        assert results[0].content == "8" and results[1].content.startswith("Error:")
    assert any(r["type"] == "extension_loaded" for r in read_trace(trace))


def test_bad_explicit_extension_fails_startup(tmp_path):
    workspace, config, options = setup(tmp_path)
    extension = workspace / "bad.py"
    extension.write_text("raise RuntimeError('bad extension')")
    options.extensions = [extension]
    with pytest.raises(ExtensionError, match="bad extension"):
        Runtime(config, options=options, model=FakeModel(), home=tmp_path / "home")


def test_explicit_skill_user_message_is_traced_in_its_run(tmp_path):
    workspace, config, options = setup(tmp_path)
    make_skill(workspace / ".agents/skills", disabled=True)
    with Runtime(config, options=options, model=FakeModel([text_turn("ok")]), home=tmp_path / "home") as runtime:
        runtime.agent.send("/skill:review-code user task")
        assert "user task" in runtime.agent.history[0].content
        records = list(read_trace(runtime.trace_path))
    loaded = next(r for r in records if r["type"] == "skill_loaded")
    start = next(r for r in records if r["type"] == "agent_start")
    assert loaded["run_id"] == start["run_id"] and loaded["data"]["explicit"]
