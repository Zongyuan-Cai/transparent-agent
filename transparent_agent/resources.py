"""Project instructions, lazy skills and explicit prompt templates."""

from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from .events import EventSink, publish
from .tools.base import Tool, ToolError, ToolResult

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    explicit_only: bool = False


def project_chain(workspace: Path) -> list[Path]:
    """Nearest directory first, stopping at a repository root."""
    chain = []
    for directory in (workspace, *workspace.parents):
        chain.append(directory)
        if (directory / ".git").exists():
            break
    return chain


class Resources:
    def __init__(self, *, workspace: Path, sink: EventSink, skill_dirs=(), prompt_dirs=(),
                 home: Path | None = None):
        self.workspace = workspace
        self.sink = sink
        self.skills: dict[str, Skill] = {}
        self.prompts: dict[str, Path] = {}
        home = home or Path.home()
        chain = project_chain(workspace)
        skill_roots = [*skill_dirs, *(p / ".agents/skills" for p in chain),
                       home / ".config/transparent-agent/skills", home / ".agents/skills"]
        prompt_roots = [*prompt_dirs, *(p / ".agents/prompts" for p in chain),
                        home / ".config/transparent-agent/prompts"]
        self._discover_skills(skill_roots)
        self._discover_prompts(prompt_roots)

    def diagnostic(self, path: Path, message: str) -> None:
        publish(self.sink, "resource_diagnostic", {"path": str(path), "message": message})

    def _roots(self, roots):
        seen = set()
        for raw in roots:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = self.workspace / path
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            if not path.is_dir():
                # Missing conventional locations are normal.
                if path.exists():
                    self.diagnostic(path, "resource root is not a directory")
                continue
            yield path

    def _discover_skills(self, roots):
        for root in self._roots(roots):
            for directory, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                if "SKILL.md" not in files:
                    continue
                path = Path(directory) / "SKILL.md"
                try:
                    if not path.resolve().is_relative_to(Path(directory).resolve()):
                        raise ValueError("SKILL.md symlink escapes its directory")
                    text = path.read_text(encoding="utf-8")
                    lines = text.splitlines()
                    if not lines or lines[0] != "---":
                        raise ValueError("missing YAML frontmatter")
                    end = lines.index("---", 1)
                    metadata = yaml.safe_load("\n".join(lines[1:end]))
                    if not isinstance(metadata, dict):
                        raise ValueError("frontmatter must be a mapping")
                    name, description = metadata.get("name"), metadata.get("description")
                    if not isinstance(name, str) or len(name) > 64 or not NAME.fullmatch(name):
                        raise ValueError("invalid skill name")
                    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
                        raise ValueError("description must contain 1..1024 characters")
                    disabled = metadata.get("disable-model-invocation", False)
                    if not isinstance(disabled, bool):
                        raise ValueError("disable-model-invocation must be boolean")
                    if name in self.skills:
                        self.diagnostic(path, f"duplicate skill {name}; keeping {self.skills[name].path}")
                        continue
                    self.skills[name] = Skill(name, description, path.resolve(), disabled)
                except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
                    self.diagnostic(path, str(exc))

    def _discover_prompts(self, roots):
        for root in self._roots(roots):
            for path in sorted(root.glob("*.md")):
                if not NAME.fullmatch(path.stem):
                    self.diagnostic(path, "invalid prompt name")
                elif path.stem in self.prompts:
                    self.diagnostic(path, f"duplicate prompt {path.stem}; keeping {self.prompts[path.stem]}")
                elif not path.resolve().is_relative_to(root):
                    self.diagnostic(path, "prompt symlink escapes resource root")
                else:
                    self.prompts[path.stem] = path.resolve()

    def instructions(self) -> str:
        lines = ["\n## Available skills", "Use load_skill to read a matching skill before following its workflow."]
        for skill in self.skills.values():
            if not skill.explicit_only:
                # JSON-style quoting prevents metadata creating fake prompt headings.
                lines.append(json.dumps({"name": skill.name, "description": skill.description,
                                         "path": str(skill.path)}, ensure_ascii=False))
        if self.prompts:
            lines.append("\nUser prompt commands: " + ", ".join(f"/prompt:{name}" for name in self.prompts))
        return "\n".join(lines) if self.skills or self.prompts else ""

    def load_skill(self, name: str, *, resource: str | None = None, explicit: bool = False) -> ToolResult:
        skill = self.skills.get(name)
        if skill is None:
            raise ToolError(f"unknown skill {name!r}")
        if skill.explicit_only and not explicit:
            raise ToolError(f"skill {name!r} requires an explicit user /skill:{name} command")
        root = skill.path.parent
        relative = Path(resource) if resource is not None else Path("SKILL.md")
        if relative.is_absolute() or ".." in relative.parts:
            raise ToolError("skill resource must be a relative path inside the skill directory")
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ToolError("skill resource escapes its directory")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ToolError(f"cannot read skill resource {path}: {exc}") from exc
        if "\x00" in content:
            raise ToolError("skill resources must be text")
        publish(self.sink, "skill_loaded", {
            "name": name, "path": str(path), "directory": str(root), "content": content,
            "explicit": explicit,
        })
        return ToolResult(f"Skill: {name}\nDirectory: {root}\nSource: {path}\n\n{content}",
                          metadata={"skill": name, "path": str(path)})

    def expand(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        command = parts[0] if parts else ""
        arguments = parts[1] if len(parts) > 1 else ""
        if command.startswith("/skill:"):
            name = command[len("/skill:"):]
            result = self.load_skill(name, explicit=True)
            return result.output + (f"\n\nUser task:\n{arguments}" if arguments else "")
        if command.startswith("/prompt:"):
            name = command[len("/prompt:"):]
            path = self.prompts.get(name)
            if path is None:
                raise ToolError(f"unknown prompt {name!r}")
            try:
                template = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ToolError(f"cannot load prompt {path}: {exc}") from exc
            expanded = template.replace("$ARGUMENTS", arguments) if "$ARGUMENTS" in template else template + ("\n\n" + arguments if arguments else "")
            publish(self.sink, "prompt_expanded", {
                "name": name, "path": str(path), "arguments": arguments, "content": expanded,
            })
            return expanded
        return text

    def tool(self) -> Tool:
        def load(args, ctx):
            return self.load_skill(args["name"], resource=args.get("resource"))
        return Tool("load_skill", "Read an available skill's SKILL.md or a text resource relative to its directory. This does not run scripts.",
                    {"type": "object", "properties": {
                        "name": {"type": "string"}, "resource": {"type": "string"}},
                     "required": ["name"], "additionalProperties": False}, load)


def build_context(workspace: Path, base: str, resources: Resources) -> str:
    sections = [base, "Project and skill instructions are contextual guidance; user instructions take precedence."]
    for directory in reversed(project_chain(workspace)):
        path = directory / "AGENTS.md"
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            resources.diagnostic(path, str(exc))
            continue
        publish(resources.sink, "context_loaded", {"source": "AGENTS.md", "path": str(path), "content": content})
        sections.append(f"\n## Project instructions from {path}\n{content}")
    sections.append(resources.instructions())
    return "\n".join(sections)
