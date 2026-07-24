"""Trace adapter for Claude Code session transcripts.

Reads home/.claude/projects/<munged-cwd>/<session>.jsonl. Skill and MCP
invocations are explicit tool_use blocks; slash commands appear as
<command-name> markers in user text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from drskill.traces.common import excerpt, parse_ts
from drskill.traces.model import ExtractResult, Invocation

HARNESS = "claude-code"
VERSION = 4

_COMMAND = re.compile(r"<command-name>/?([^<\s]+)</command-name>")

# Claude Code built-in CLI commands are not skills. Not exhaustive; unknown
# builtins surface as low-count command-marker rows a user can ignore.
BUILTIN_COMMANDS = frozenset({
    "clear", "help", "compact", "config", "cost", "doctor", "exit", "login",
    "logout", "memory", "model", "quit", "resume", "status", "init", "bug",
    "release-notes", "terminal-setup", "vim", "permissions", "hooks", "mcp",
    "agents", "todos", "add-dir", "context", "export", "rewind", "usage",
})


def discover(home: Path) -> list[Path]:
    root = home / ".claude" / "projects"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.jsonl"))


def _text_blocks(content: object) -> list[str]:
    if not isinstance(content, list):
        return []
    return [b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"]


def extract(path: Path) -> ExtractResult:
    out: list[Invocation] = []
    recognized = 0
    last_query: str | None = None
    prev_thinking: str | None = None  # from the previous assistant message
    lines = path.read_text(errors="replace").splitlines()
    for lineno, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if etype not in ("user", "assistant"):
            continue
        message = event.get("message") or {}
        content = message.get("content")
        if etype == "user" and isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        recognized += 1
        base = dict(
            harness=HARNESS,
            session_id=str(event.get("sessionId", path.stem)),
            project=event.get("cwd") if isinstance(event.get("cwd"), str) else None,
            timestamp=parse_ts(event.get("timestamp")),
            sidechain=bool(event.get("isSidechain")),
            source_file=str(path),
            source_line=lineno,
        )
        if base["timestamp"] is None:
            continue
        if etype == "user":
            texts = _text_blocks(content)
            if texts and not event.get("isSidechain") and not event.get("isMeta"):
                last_query = texts[0]
            for text in texts:
                for m in _COMMAND.finditer(text):
                    name = m.group(1)
                    if name in BUILTIN_COMMANDS:
                        continue
                    out.append(Invocation(
                        **base, kind="skill", name=name,
                        query=excerpt(last_query), detection="command-marker",
                    ))
            continue
        # assistant: walk blocks in order so thinking precedes its tool_use
        current_thinking = prev_thinking
        saw_thinking = False
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "thinking":
                text = block.get("thinking") or ""
                if text.strip():
                    current_thinking = text
                    saw_thinking = True
                continue
            if block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            inp = block.get("input") or {}
            if name == "Skill" and isinstance(inp, dict) and inp.get("skill"):
                out.append(Invocation(
                    **base, kind="skill", name=str(inp["skill"]),
                    query=excerpt(last_query),
                    reasoning=excerpt(current_thinking), detection="explicit",
                ))
            elif name.startswith("mcp__"):
                parts = name.split("__")
                if len(parts) >= 3:
                    out.append(Invocation(
                        **base, kind="mcp_tool", server=parts[1],
                        name="__".join(parts[2:]),
                        query=excerpt(last_query),
                        reasoning=excerpt(current_thinking), detection="explicit",
                    ))
        # Only a message that had real thinking feeds the fallback for the next one.
        prev_thinking = current_thinking if saw_thinking else prev_thinking
    return ExtractResult(invocations=out, recognized=recognized)
