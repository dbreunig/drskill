"""Trace adapter for Copilot chat sessions in VS Code.

Reads workspaceStorage/<hash>/chatSessions/<uuid>.json. Tool arguments are
stored as prose upstream, so only tool identity is recovered. No reasoning
is recorded by this harness.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from drskill.traces.common import excerpt
from drskill.traces.model import ExtractResult, Invocation

HARNESS = "copilot"
VERSION = 1


def discover(home: Path) -> list[Path]:
    root = (home / "Library" / "Application Support" / "Code" / "User"
            / "workspaceStorage")
    if not root.is_dir():
        return []
    return sorted(root.glob("*/chatSessions/*.json"))


def _epoch_ms(value: object) -> dt.datetime | None:
    if not isinstance(value, (int, float)):
        return None
    return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc)


def _project_for(path: Path) -> str | None:
    ws = path.parent.parent / "workspace.json"
    try:
        data = json.loads(ws.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    folder = data.get("folder")
    if isinstance(folder, str) and folder.startswith("file://"):
        return unquote(urlparse(folder).path)
    return None


def extract(path: Path) -> ExtractResult:
    try:
        data = json.loads(path.read_text(errors="replace"))
    except ValueError:
        return ExtractResult()
    if not isinstance(data, dict):
        return ExtractResult()
    out: list[Invocation] = []
    recognized = 0
    session_id = str(data.get("sessionId", path.stem))
    project = _project_for(path)
    fallback_ts = _epoch_ms(data.get("creationDate"))
    for request in data.get("requests") or []:
        if not isinstance(request, dict):
            continue
        recognized += 1
        message = request.get("message") or {}
        query = message.get("text") if isinstance(message, dict) else None
        ts = _epoch_ms(request.get("timestamp")) or fallback_ts
        if ts is None:
            continue
        base = dict(
            harness=HARNESS, session_id=session_id, project=project,
            timestamp=ts, sidechain=False, source_file=str(path),
        )
        for part in request.get("response") or []:
            if not isinstance(part, dict):
                continue
            if part.get("kind") != "toolInvocationSerialized":
                continue
            tool_id = part.get("toolId", "")
            if not isinstance(tool_id, str):
                continue
            if tool_id.startswith("mcp_") and tool_id.count("_") >= 2:
                _, server, tool = tool_id.split("_", 2)
                if server and tool:
                    out.append(Invocation(
                        **base, kind="mcp_tool", server=server, name=tool,
                        query=excerpt(query), detection="explicit",
                    ))
            elif tool_id == "skill":
                name = part.get("toolSpecificData")
                if isinstance(name, str) and name:
                    out.append(Invocation(
                        **base, kind="skill", name=name,
                        query=excerpt(query), detection="explicit",
                    ))
    return ExtractResult(invocations=out, recognized=recognized)
