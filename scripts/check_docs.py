#!/usr/bin/env python3
"""Check local Markdown links in the documentation library and README."""

from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    files = [root / "README.md", *sorted((root / "docs").rglob("*.md"))]
    missing = []
    for file in files:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            target = target.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            destination = (file.parent / unquote(parsed.path)).resolve()
            if not destination.exists():
                missing.append(f"{file.relative_to(root)}: {target}")
    if missing:
        print("\n".join(missing))
        return 1
    print(f"Validated local links in {len(files)} Markdown documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
