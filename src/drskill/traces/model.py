"""The audit unit of analysis: one skill or MCP tool invocation seen in a trace."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


class Invocation(BaseModel):
    harness: str  # claude-code | codex | pi | copilot
    session_id: str
    project: str | None = None  # cwd from trace metadata, None if unknowable
    timestamp: dt.datetime
    kind: Literal["skill", "mcp_tool"]
    name: str
    server: str | None = None  # MCP server, only when kind == "mcp_tool"
    query: str | None = None  # excerpt of the user message that opened the turn
    reasoning: str | None = None  # excerpt of the nearest preceding thinking text
    sidechain: bool = False
    detection: Literal["explicit", "skill-read", "command-marker"]
    source_file: str  # the trace file, evidence for drill-downs
    source_line: int | None = None  # 1-based JSONL line of the producing event


class ExtractResult(BaseModel):
    invocations: list[Invocation] = Field(default_factory=list)
    recognized: int = 0  # count of events the adapter understood; 0 flags format drift
