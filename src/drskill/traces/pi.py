"""Trace adapter for pi agent sessions.

Reads home/.pi/agent/sessions/<slug>/<ts>_<uuid>.jsonl. Skills surface as
read or bash tool calls on SKILL.md paths. Thinking is plaintext. MCP calls
were absent on the researched machine; the mcp__server__tool name pattern is
handled so they are recorded when they appear.
"""

from __future__ import annotations

import json
from pathlib import Path

from drskill.traces.common import excerpt, parse_ts, skill_md_names
from drskill.traces.model import ExtractResult, Invocation

HARNESS = "pi"
VERSION = 4


def discover(home: Path) -> list[Path]:
    root = home / ".pi" / "agent" / "sessions"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.jsonl"))


def extract(path: Path) -> ExtractResult:
    out: list[Invocation] = []
    recognized = 0
    session_id = path.stem
    project: str | None = None
    last_query: str | None = None
    prev_thinking: str | None = None
    for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "session":
            recognized += 1
            session_id = str(event.get("id", session_id))
            if isinstance(event.get("cwd"), str):
                project = event["cwd"]
            continue
        if event.get("type") != "message":
            continue
        message = event.get("message") or {}
        content = message.get("content")
        role = message.get("role")
        if not isinstance(content, list) or role not in ("user", "assistant"):
            continue
        recognized += 1
        ts = parse_ts(event.get("timestamp"))
        if ts is None:
            continue
        if role == "user":
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                last_query = texts[0]
            continue
        current_thinking = prev_thinking
        saw_thinking = False
        base = dict(
            harness=HARNESS, session_id=session_id, project=project,
            timestamp=ts, sidechain=False, source_file=str(path),
            source_line=lineno,
        )
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "thinking":
                text = block.get("thinking") or ""
                if text.strip():
                    current_thinking = text
                    saw_thinking = True
                continue
            if block.get("type") != "toolCall":
                continue
            name = block.get("name", "")
            args = block.get("arguments") or {}
            if name.startswith("mcp__"):
                parts = name.split("__")
                if len(parts) >= 3:
                    out.append(Invocation(
                        **base, kind="mcp_tool", server=parts[1],
                        name="__".join(parts[2:]), query=last_query,
                        query_source="user" if last_query is not None else None,
                        reasoning=excerpt(current_thinking), detection="explicit",
                    ))
                continue
            if name in ("read", "bash") and isinstance(args, dict):
                text = str(args.get("path", "")) + " " + str(args.get("command", ""))
                for skill in skill_md_names(text):
                    out.append(Invocation(
                        **base, kind="skill", name=skill,
                        query=last_query,
                        query_source="user" if last_query is not None else None,
                        reasoning=excerpt(current_thinking), detection="skill-read",
                    ))
        prev_thinking = current_thinking if saw_thinking else prev_thinking
    return ExtractResult(invocations=out, recognized=recognized)
