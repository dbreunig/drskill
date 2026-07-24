"""Trace adapter for Codex rollout files.

Reads home/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl. MCP calls are explicit
mcp_tool_call_end events. Skills have no event of their own; the signal is a
SKILL.md path inside a function_call or custom_tool_call payload. Reasoning is
encrypted upstream, so it is always None here.
"""

from __future__ import annotations

import json
from pathlib import Path

from drskill.traces.common import excerpt, parse_ts, skill_md_names
from drskill.traces.model import ExtractResult, Invocation

HARNESS = "codex"
VERSION = 1


def discover(home: Path) -> list[Path]:
    root = home / ".codex" / "sessions"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*/*/rollout-*.jsonl"))


def extract(path: Path) -> ExtractResult:
    out: list[Invocation] = []
    recognized = 0
    session_id = path.stem
    project: str | None = None
    sidechain = False
    last_query: str | None = None
    for line in path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        etype = event.get("type")
        ptype = payload.get("type")
        ts = parse_ts(event.get("timestamp"))
        if etype == "session_meta":
            recognized += 1
            session_id = str(payload.get("id") or payload.get("session_id") or session_id)
            if isinstance(payload.get("cwd"), str):
                project = payload["cwd"]
            source = payload.get("source")
            sidechain = payload.get("thread_source") == "subagent" or (
                isinstance(source, dict) and "subagent" in source
            )
            continue
        if ts is None:
            continue
        base = dict(
            harness=HARNESS, session_id=session_id, project=project,
            timestamp=ts, sidechain=sidechain, source_file=str(path),
        )
        if etype == "event_msg" and ptype == "user_message":
            recognized += 1
            if isinstance(payload.get("message"), str):
                last_query = payload["message"]
        elif etype == "event_msg" and ptype == "mcp_tool_call_end":
            recognized += 1
            invocation = payload.get("invocation") or {}
            server = invocation.get("server")
            tool = invocation.get("tool")
            if isinstance(server, str) and isinstance(tool, str):
                out.append(Invocation(
                    **base, kind="mcp_tool", server=server, name=tool,
                    query=excerpt(last_query), detection="explicit",
                ))
        elif etype == "response_item" and ptype in ("function_call", "custom_tool_call"):
            recognized += 1
            text = payload.get("arguments") if ptype == "function_call" else payload.get("input")
            if isinstance(text, str):
                for skill in skill_md_names(text):
                    out.append(Invocation(
                        **base, kind="skill", name=skill,
                        query=excerpt(last_query), detection="skill-read",
                    ))
    return ExtractResult(invocations=out, recognized=recognized)
