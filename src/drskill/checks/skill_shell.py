"""Invocation-time shell commands embedded in skill files.

Claude Code runs `` !`command` `` placeholders and ```! fenced blocks in a
skill file before the model sees the content (dynamic context injection).
This module extracts those commands and checks them: an approval baseline
with a rug-pull diff, and an immediate scan against the dangerous-content
lexicons in checks/injection.py."""

from __future__ import annotations

import re

from drskill.checks import injection
from drskill.models import Contributor

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
