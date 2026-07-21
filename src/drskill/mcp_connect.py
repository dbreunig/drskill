"""The MCP handshake: connect to configured servers, enumerate their tools,
and snapshot the results. Everything that speaks the protocol lives behind a
lazy `mcp` SDK import in `connect_server`. Snapshots are value-free JSON;
no env value or secret is ever written."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class ToolInfo(BaseModel):
    name: str
    description: str
    schema_tokens: int


class ServerSnapshot(BaseModel):
    server: str
    config_hash: str
    date: str  # ISO date of the handshake
    tools: list[ToolInfo] = Field(default_factory=list)


def snapshot_dir(project_root: Path, home: Path, global_mode: bool) -> Path:
    base = home if global_mode else project_root
    return base / ".drskill" / "cache" / "mcp-tools"


def load_snapshots(sdir: Path) -> dict[str, ServerSnapshot]:
    out: dict[str, ServerSnapshot] = {}
    if not sdir.is_dir():
        return out
    for p in sorted(sdir.glob("*.json")):
        try:
            snap = ServerSnapshot(**json.loads(p.read_text()))
        except Exception:
            continue
        out[snap.config_hash] = snap
    return out


def save_snapshot(sdir: Path, snap: ServerSnapshot) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"{snap.config_hash}.json").write_text(snap.model_dump_json(indent=2) + "\n")


def changed_tools(old: ServerSnapshot | None, new: ServerSnapshot) -> list[str]:
    if old is None:
        return []
    old_desc = {t.name: t.description for t in old.tools}
    new_desc = {t.name: t.description for t in new.tools}
    changed = [
        n for n in set(old_desc) | set(new_desc)
        if old_desc.get(n) != new_desc.get(n)
    ]
    return sorted(changed)


def tool_fingerprint_base(snap: ServerSnapshot) -> list[str]:
    return sorted(f"{t.name}\n{t.description}" for t in snap.tools)
