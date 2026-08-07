"""drskill lint: check one authorable unit (an Agent Plugins plugin, a
skill, or an MCP config file) against its standard and drskill's checks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

_ACCEPTS = (
    "drskill lint takes a plugin directory (with plugin.json), a skill "
    "directory or SKILL.md file, or an MCP config JSON file"
)
_MCP_SCHEMA_RE = re.compile(r"agent-plugins\.org/schemas/[^/]+/mcp\.schema\.json$")


class LintUsageError(Exception):
    pass


class LintTarget(BaseModel):
    kind: Literal["plugin", "skill", "mcp"]
    path: Path
    mcp_flavor: Literal["agent-plugins", "harness"] | None = None


def classify(path: Path, forced: str | None = None) -> LintTarget:
    p = path.expanduser()
    if not p.exists():
        raise LintUsageError(f"{path} does not exist; {_ACCEPTS}")
    if forced == "plugin":
        if not (p.is_dir() and (p / "plugin.json").is_file()):
            raise LintUsageError(f"{path} is not a plugin directory (no plugin.json)")
        return LintTarget(kind="plugin", path=p)
    if forced == "skill":
        f = p if p.is_file() else p / "SKILL.md"
        if not f.is_file():
            raise LintUsageError(f"{path} is not a skill (no SKILL.md)")
        return LintTarget(kind="skill", path=p)
    if forced == "mcp":
        if not p.is_file():
            raise LintUsageError(f"{path} is not an MCP config file")
        return _classify_json(p)
    if p.is_dir():
        if (p / "plugin.json").is_file():
            return LintTarget(kind="plugin", path=p)
        if (p / "SKILL.md").is_file():
            return LintTarget(kind="skill", path=p)
        raise LintUsageError(f"{path} has no plugin.json or SKILL.md; {_ACCEPTS}")
    if p.name == "SKILL.md":
        return LintTarget(kind="skill", path=p)
    if p.suffix == ".json" or p.name.startswith("."):
        return _classify_json(p)
    raise LintUsageError(f"{path} is not a lintable file; {_ACCEPTS}")


def _classify_json(p: Path) -> LintTarget:
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        data = None
    sibling_manifest = (p.parent / "plugin.json").is_file()
    schema = data.get("$schema") if isinstance(data, dict) else None
    if isinstance(schema, str) and _MCP_SCHEMA_RE.search(schema):
        return LintTarget(kind="mcp", path=p, mcp_flavor="agent-plugins")
    if sibling_manifest and p.name == "mcp.json":
        return LintTarget(kind="mcp", path=p, mcp_flavor="agent-plugins")
    if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        return LintTarget(kind="mcp", path=p, mcp_flavor="harness")
    if data is None and p.name in ("mcp.json", ".mcp.json"):
        flavor = "agent-plugins" if sibling_manifest else "harness"
        return LintTarget(kind="mcp", path=p, mcp_flavor=flavor)
    raise LintUsageError(f"{p} is not a recognized MCP config; {_ACCEPTS}")
