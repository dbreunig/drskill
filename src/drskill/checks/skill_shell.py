"""Invocation-time shell commands embedded in skill files.

Claude Code runs `` !`command` `` placeholders and ```! fenced blocks in a
skill file before the model sees the content (dynamic context injection).
This module extracts those commands and checks them: an approval baseline
with a rug-pull diff, and an immediate scan against the dangerous-content
lexicons in checks/injection.py."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from drskill.checks import injection
from drskill.models import Contributor, ShellBaseline

# The harness only recognizes `!` at line start or immediately after
# whitespace; KEY=!`cmd` stays literal text and never runs.
_INLINE = re.compile(r"(?:^|(?<=\s))!`([^`\n]+)`")


def extract_commands(text: str) -> list[tuple[int, str]]:
    """Ordered (1-based line number, command) pairs for every embedded
    shell command: inline !`cmd` and lines inside ```! fenced blocks."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for i, line in enumerate(injection._split_lines(text), start=1):
        stripped = line.strip()
        if in_fence:
            if stripped == "```":
                in_fence = False
            elif stripped:
                out.append((i, stripped))
        elif stripped == "```!":
            in_fence = True
        else:
            out.extend((i, m.group(1)) for m in _INLINE.finditer(line))
    return out


def _skillmd(c: Contributor) -> injection.Source | None:
    """The SKILL.md source from the shared scan-view cache; None for MCP
    tools and unreadable skills."""
    return next((s for s in injection.scan_view(c) if s.kind == "skillmd"), None)


def shell_dir(project_root: Path, home: Path, global_mode: bool) -> Path:
    base = home if global_mode else project_root
    return base / ".drskill" / "cache" / "skill-shell"


def _norm_path(p: Path, project_root: Path, home: Path) -> str:
    """Portable form of a skill path: project checkouts and home dirs move
    between machines, so the key must not bake in either prefix."""
    try:
        return "./" + p.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        pass
    try:
        return "~/" + p.relative_to(home.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def baseline_key(c: Contributor, project_root: Path, home: Path) -> str:
    """Identity hash for the baseline file: survives command changes (it is
    content-free) and machine moves (the path is normalized)."""
    ident = c.name + "\n" + _norm_path(Path(c.id), project_root, home)
    return hashlib.sha256(ident.encode()).hexdigest()


def load_baselines(bdir: Path) -> dict[str, ShellBaseline]:
    out: dict[str, ShellBaseline] = {}
    if not bdir.is_dir():
        return out
    for p in sorted(bdir.glob("*.json")):
        try:
            out[p.stem] = ShellBaseline.model_validate_json(p.read_text())
        except (OSError, ValueError):
            continue  # corrupt entries are prune's job
    return out
