#!/usr/bin/env python3
"""End-to-end acceptance runs against a real model.

Each scenario creates a throwaway workspace, runs the agent on it, and then
checks what the agent actually left on disk (and in its event stream).

Usage:
    OPENAI_API_KEY=... OPENAI_BASE_URL=... OPENAI_MODEL=... python3 scripts/e2e.py
    python3 scripts/e2e.py --only A,B --keep

Transcripts are written to .e2e/<scenario>.txt; workspaces are removed unless
--keep is given.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transparent_agent import events  # noqa: E402
from transparent_agent.agent import Agent, FINAL  # noqa: E402
from transparent_agent.events import CompositeSink  # noqa: E402
from transparent_agent.config import Config  # noqa: E402
from transparent_agent.models import create_model  # noqa: E402
from transparent_agent.prompt import build_system_prompt  # noqa: E402
from transparent_agent.tools import ToolContext, build_registry  # noqa: E402
from transparent_agent.tui.render import Renderer, Style  # noqa: E402

VENV_PY = sys.executable


# ---------------------------------------------------------------- utilities

class Tee(io.TextIOBase):
    """Write to a live stream and keep a copy for the transcript file."""

    def __init__(self, live):
        self.live = live
        self.buffer_text = io.StringIO()

    def write(self, text):
        self.live.write(text)
        self.live.flush()
        self.buffer_text.write(text)
        return len(text)

    def flush(self):
        self.live.flush()

    def transcript(self) -> str:
        return self.buffer_text.getvalue()


class Recorder:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)

    def of_type(self, event_type):
        return [event for event in self.events if event.type == event_type]


@dataclass
class Run:
    workspace: Path
    agent: object
    recorder: Recorder = None
    turns: list = field(default_factory=list)
    transcript: str = ""
    duration: float = 0.0

    @property
    def records(self) -> Recorder:
        return self.recorder

    @property
    def final_text(self) -> str:
        return self.turns[-1].text if self.turns else ""


class CheckFailed(Exception):
    pass


def check(condition, message):
    if not condition:
        raise CheckFailed(message)


def pytest_passes(workspace: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [VENV_PY, "-m", "pytest", "-q"], cwd=workspace,
        capture_output=True, text=True, timeout=180,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-1500:]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    """relative path -> content hash, ignoring caches and VCS metadata."""
    files = {}
    for path in sorted(root.rglob("*")):
        if any(part in {".git", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        if path.is_file():
            files[str(path.relative_to(root))] = file_hash(path)
    return files


def write_files(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def command_outputs(records: Recorder) -> list[str]:
    return [event.data["output"] for event in records.of_type(events.TOOL_CALL_END)
            if event.data["name"] == "bash"]


def looks_failed(output: str) -> bool:
    # a pipeline like `pytest | head` exits 0, so the text matters as much as the code
    if "exit_code: 0" not in output:
        return True
    return any(marker in output for marker in ("SyntaxError", "ERROR collecting", "FAILED", "Traceback"))


def bash_outputs(records: Recorder) -> str:
    return "\n".join(command_outputs(records))


# ---------------------------------------------------------------- scenarios

@dataclass
class Scenario:
    key: str
    title: str
    files: dict[str, str]
    tasks: list[str]
    verify: object  # callable(Run, Path) -> None, raises CheckFailed
    no_sink: bool = False


# -- A: repository understanding -------------------------------------------

A_FILES = {
    "README.md": """# Notes CLI

A tiny notes manager. Notes are kept in memory during a run and can be
listed or added from the command line.
""",
    "notes/__init__.py": '"""Notes package."""\n',
    "notes/store.py": '''"""In-memory note storage."""


class NotesStore:
    def __init__(self):
        self._notes = []

    def add(self, text):
        self._notes.append(text)
        return len(self._notes)

    def list(self):
        return list(self._notes)
''',
    "notes/cli.py": '''"""Command line interface for the notes manager."""

import argparse

from .store import NotesStore


def build_parser():
    parser = argparse.ArgumentParser(prog="notes")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("text")
    sub.add_parser("list")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    store = NotesStore()
    if args.command == "add":
        index = store.add(args.text)
        print(f"added note {index}")
    else:
        for note in store.list():
            print(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    "notes/__main__.py": "from .cli import main\n\nif __name__ == \"__main__\":\n    raise SystemExit(main())\n",
    "pyproject.toml": "[project]\nname = \"notes\"\nversion = \"0.1.0\"\n\n[project.scripts]\nnotes = \"notes.cli:main\"\n",
}


def verify_a(run: Run, before: str) -> None:
    check(run.turns[-1].stop_reason == FINAL, f"turn did not finish: {run.turns[-1].stop_reason}")
    text = run.final_text.lower()
    check("cli.py" in text, "entry point file (notes/cli.py) not identified")
    check("main" in text, "entry point function (main) not identified")
    check(snapshot(run.workspace) == before, "workspace was modified during a read-only task")


# -- B: single-file bug fix -------------------------------------------------

B_FILES = {
    "textkit.py": '''"""Small text utilities."""


def word_count(text):
    return len(text.split())


def truncate(text, limit):
    """Shorten text so it fits in `limit` characters."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
''',
    "test_textkit.py": '''from textkit import truncate, word_count


def test_word_count():
    assert word_count("one two three") == 3


def test_truncate_keeps_short_text():
    assert truncate("hello", 10) == "hello"


def test_truncate_respects_limit():
    result = truncate("a" * 100, 10)
    assert len(result) <= 10
    assert result.endswith("...")
''',
}


def verify_b(run: Run, tests_before: dict) -> None:
    passed, output = pytest_passes(run.workspace)
    check(passed, f"tests still fail after the run:\n{output}")
    check(file_hash(run.workspace / "test_textkit.py") == tests_before["test_textkit.py"],
          "the test file was modified")


# -- C: multi-file feature --------------------------------------------------

C_FILES = {
    "pyproject.toml": "[tool.pytest.ini_options]\npythonpath = [\".\"]\n",
    "greeter/__init__.py": 'from .core import greet\n\n__all__ = ["greet"]\n',
    "greeter/core.py": 'def greet(name):\n    return f"Hello, {name}!"\n',
    "tests/test_greeter.py": 'from greeter import greet\n\n\ndef test_greet():\n    assert greet("ada") == "Hello, ada!"\n',
}


def verify_c(run: Run, tests_before: str) -> None:
    proc = subprocess.run(
        [VENV_PY, "-c", "from greeter import shout; assert shout('ada') == 'HELLO, ADA!', shout('ada'); print('ok')"],
        cwd=run.workspace, capture_output=True, text=True, timeout=60,
    )
    check(proc.returncode == 0, f"`from greeter import shout` does not work: {proc.stderr.strip()}")
    passed, output = pytest_passes(run.workspace)
    check(passed, f"tests fail after the run:\n{output}")
    test_files = [path for path in run.workspace.rglob("test_*.py")] + \
                 [path for path in run.workspace.rglob("*_test.py")]
    total = sum(path.read_text(errors="replace").count("def test_") for path in test_files)
    check(total >= 2, "no new test was added")


# -- D: new file creation ---------------------------------------------------

D_INPUT = "alpha beta\ngamma delta\nepsilon zeta\n"  # 3 lines, 6 words


def verify_d(run: Run, tests_before: str) -> None:
    script = run.workspace / "scripts/wordcount.py"
    check(script.is_file(), "scripts/wordcount.py was not created")
    sample = run.workspace / "verification_input.txt"
    sample.write_text(D_INPUT, encoding="utf-8")
    proc = subprocess.run(
        [VENV_PY, str(script), str(sample)], cwd=run.workspace,
        capture_output=True, text=True, timeout=60,
    )
    check(proc.returncode == 0, f"script failed: {proc.stderr.strip()}")
    check("3" in proc.stdout, f"line count missing from output: {proc.stdout!r}")
    check("6" in proc.stdout, f"word count missing from output: {proc.stdout!r}")


# -- E: failure recovery ----------------------------------------------------

E_FILES = {
    "report.py": '''"""Build a summary report from rows."""


def build_report(rows):
    total = sum(row["amount"] for row in rows
    return {"total": total, "count": len(rows)}
''',
    "test_report.py": '''from report import build_report


def test_build_report():
    assert build_report([{"amount": 2}, {"amount": 3}]) == {"total": 5, "count": 2}
''',
}


def verify_e(run: Run, tests_before: str) -> None:
    passed, output = pytest_passes(run.workspace)
    check(passed, f"tests still fail after the run:\n{output}")
    outputs = command_outputs(run.records)
    failure_at = next((index for index, text in enumerate(outputs) if looks_failed(text)), None)
    passing_at = next((index for index, text in enumerate(outputs) if " passed" in text), None)
    check(failure_at is not None, "no failing command was observed")
    check(passing_at is not None and passing_at > failure_at,
          "the agent never recovered to a passing test run")
    combined = bash_outputs(run.records)
    check("SyntaxError" in combined or "ERROR collecting" in combined,
          "the broken collection was never observed in command output")


# -- F: follow-up conversation ---------------------------------------------

def verify_f(run: Run, tests_before: dict) -> None:
    passed, output = pytest_passes(run.workspace)
    check(passed, f"tests fail after the follow-up:\n{output}")
    test_files = [path for path in run.workspace.rglob("test_*.py")] + \
                 [path for path in run.workspace.rglob("*_test.py")]
    total = sum(path.read_text(errors="replace").count("def test_") for path in test_files)
    check(total >= 4, f"no regression test was added (project has {total} tests)")
    # the fix from the first turn must still hold after the follow-up
    proc = subprocess.run(
        [VENV_PY, "-c", "from textkit import truncate; r = truncate('a' * 100, 10); "
                        "assert len(r) <= 10, r; print('ok')"],
        cwd=run.workspace, capture_output=True, text=True, timeout=60,
    )
    check(proc.returncode == 0, f"the fix no longer holds: {proc.stderr.strip()}")
    # the second turn must send the first turn's conversation to the model again
    requests = run.records.of_type(events.MODEL_REQUEST_START)
    check(len(requests) > 1, "the follow-up produced no further model request")
    first_task = "Run the tests, find out why they fail"
    carried = [m for m in requests[-1].data["messages"]
               if (m.get("content") or "").startswith(first_task)]
    check(carried, "the follow-up request did not carry the earlier conversation")
    check(any(m["role"] == "tool" for m in requests[-1].data["messages"]),
          "the follow-up request did not carry earlier tool results")
    # the follow-up happened in the same session, so history kept growing
    roles = [message.role for message in run.agent.history]
    check(roles.count("user") == 2, f"expected two user turns in one session, got {roles}")


# -- G: the real CLI --------------------------------------------------------

G_TASK = ("Create a file named hello.txt that contains exactly the single word "
          "'greetings', then verify the file by reading it back.")


def verify_g(run: Run, tests_before: str) -> None:
    target = run.workspace / "hello.txt"
    check(target.is_file(), "hello.txt was not created by the CLI run")
    check("greetings" in target.read_text(), "hello.txt does not contain the expected word")
    check(run.agent.process.returncode == 0, "the CLI exited with a non-zero status")
    check("greetings" in run.agent.process.stdout or "hello.txt" in run.agent.process.stdout,
          "the CLI printed no assistant answer")


SCENARIOS = [
    Scenario("A", "repository understanding", A_FILES,
             ["Explain what this project does and identify its entry point."], verify_a),
    Scenario("A2", "no event consumer attached", A_FILES,
             ["Explain what this project does and identify its entry point."], verify_a, no_sink=True),
    Scenario("B", "single-file bug fix", B_FILES,
             ["Run the tests, find out why they fail, and fix the source code so they pass. "
              "Do not modify the test file."], verify_b),
    Scenario("C", "multi-file feature", C_FILES,
             ["Add a shout(name) function to greeter/core.py that returns the greeting in upper "
              "case (shout('ada') should be 'HELLO, ADA!'). Export it from the greeter package so "
              "that `from greeter import shout` works, and add a test for it."], verify_c),
    Scenario("D", "new file creation", {},
             ["Create a script scripts/wordcount.py that takes a file path as its first argument "
              "and prints how many lines and how many words the file contains. Create a sample "
              "input file and verify the script works on it."], verify_d),
    Scenario("E", "failure recovery", E_FILES,
             ["Start by running the test suite to see the current state. The suite does not even "
              "collect; find out why, fix the code, and make sure the tests pass."], verify_e),
    Scenario("F", "follow-up conversation", B_FILES,
             ["Run the tests, find out why they fail, and fix the source code so they pass. "
              "Do not modify the test file.",
              "Now add a regression test that covers the exact bug you just fixed."], verify_f),
    Scenario("G", "CLI smoke test", {}, [G_TASK], verify_g),
]


# ---------------------------------------------------------------- runner

def run_scenario(scenario: Scenario, args) -> tuple[bool, list[str], dict]:
    workspace = Path(tempfile.mkdtemp(prefix=f"ta-e2e-{scenario.key}-"))
    write_files(workspace, scenario.files)
    before = snapshot(workspace)

    tee = Tee(sys.stdout)
    style = Style(enabled=False)
    renderer = Renderer(style=style, out=tee, log=tee)
    recorder = Recorder()

    config = Config(
        workspace=workspace,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        max_steps=args.max_steps,
    )
    model = create_model(
        model=config.model, api_key=config.api_key, base_url=config.base_url,
        timeout=config.request_timeout, stream_usage=config.stream_usage,
    )
    agent = Agent(
        model=model,
        tools=build_registry(),
        tool_context=ToolContext(workspace=workspace, max_output_chars=config.max_output_chars),
        system_prompt=build_system_prompt(workspace),
        max_steps=config.max_steps,
        sink=recorder if scenario.no_sink else CompositeSink([renderer, recorder]),
    )

    run = Run(workspace=workspace, agent=agent, recorder=recorder, transcript="")
    started = time.monotonic()
    failures: list[str] = []
    stats = {"steps": 0, "tokens": 0}

    def watchdog(signum, frame):
        raise TimeoutError(f"scenario exceeded {args.timeout}s")

    signal.signal(signal.SIGALRM, watchdog)
    signal.alarm(args.timeout)
    try:
        if scenario.key == "G":
            return run_cli_scenario(scenario, workspace, before, args, started, tee)
        for task in scenario.tasks:
            result = agent.send(task)
            run.turns.append(result)
            stats["steps"] += result.steps
            stats["tokens"] += result.usage.total_tokens
        try:
            scenario.verify(run, before)
        except CheckFailed as exc:
            failures.append(str(exc))
    except Exception as exc:  # noqa: BLE001 - report any unexpected failure
        failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        signal.alarm(0)
        run.duration = time.monotonic() - started
        run.transcript = tee.transcript()
        save_transcript(scenario.key, run.transcript)
        stats["duration"] = round(run.duration, 1)
        if args.keep:
            print(f"  workspace kept: {workspace}")
        else:
            shutil.rmtree(workspace, ignore_errors=True)
    return not failures, failures, stats


def run_cli_scenario(scenario, workspace, before, args, started, tee):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    env["OPENAI_API_KEY"] = args.api_key or ""
    if args.base_url:
        env["OPENAI_BASE_URL"] = args.base_url
    env["OPENAI_MODEL"] = args.model
    process = subprocess.run(
        [VENV_PY, "-m", "transparent_agent", "-p", scenario.tasks[0], "-C", str(workspace)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=args.timeout,
    )
    tee.write(process.stdout)
    if process.stderr.strip():
        tee.write("--- stderr ---\n" + process.stderr)

    class _Shell:
        pass

    agent = _Shell()
    agent.process = process
    run = Run(workspace=workspace, agent=agent, recorder=None,
              transcript=process.stdout + process.stderr)
    run.duration = time.monotonic() - started
    failures = []
    try:
        scenario.verify(run, before)
    except CheckFailed as exc:
        failures.append(str(exc))
    shutil.rmtree(workspace, ignore_errors=True)
    return not failures, failures, {"steps": 0, "tokens": 0, "duration": round(run.duration, 1)}


def save_transcript(key: str, text: str) -> None:
    directory = ROOT / ".e2e"
    directory.mkdir(exist_ok=True)
    (directory / f"{key}.txt").write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="comma-separated scenario keys, e.g. A,B")
    parser.add_argument("--keep", action="store_true", help="keep scenario workspaces")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"))
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=600, help="per-scenario timeout in seconds")
    args = parser.parse_args()

    if not args.api_key:
        print("error: set OPENAI_API_KEY (or pass --api-key)", file=sys.stderr)
        return 2

    selected = SCENARIOS
    if args.only:
        wanted = {key.strip().upper() for key in args.only.split(",")}
        selected = [scenario for scenario in SCENARIOS if scenario.key in wanted]

    print(f"model: {args.model}  base_url: {args.base_url or 'default'}")
    results = []
    for scenario in selected:
        print(f"\n{'=' * 70}\n[{scenario.key}] {scenario.title}\n{'=' * 70}")
        ok, failures, stats = run_scenario(scenario, args)
        results.append((scenario, ok, failures, stats))
        status = "PASS" if ok else "FAIL"
        print(f"[{scenario.key}] {status} "
              f"(steps {stats.get('steps', 0)}, tokens {stats.get('tokens', 0)}, {stats.get('duration', 0)}s)")
        for failure in failures:
            print(f"    ! {failure}")

    print(f"\n{'=' * 70}\nsummary\n{'=' * 70}")
    for scenario, ok, failures, stats in results:
        print(f"{scenario.key:<4} {'PASS' if ok else 'FAIL'}  {scenario.title}"
              f"  ({stats.get('steps', 0)} steps, {stats.get('tokens', 0)} tokens, {stats.get('duration', 0)}s)")
    failed = [scenario.key for scenario, ok, _, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
