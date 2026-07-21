"""MCP server discovery: the static half. Parses each harness's MCP config
files read-only. Secret-shaped env values are detected here in the parser
and immediately discarded; no secret value ever exists on a model, in a
fingerprint, or in any output."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

_SECRET_PREFIXES = (
    "sk-", "sk_live_", "pk_live_", "ghp_", "github_pat_", "gho_",
    "xoxb-", "xoxp-", "xapp-", "AKIA", "ASIA", "glpat-", "AIza", "ntn_",
)
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?)$", re.IGNORECASE)
# Hashes, fingerprints, and public keys are public material, not secrets.
# Real-machine tuning 2026-07-21: a SHA256 allowlist variable tripped the
# entropy rule. A known secret prefix still wins over this exclusion.
_PUBLIC_NAME = re.compile(r"(SHA\d*|HASH|FINGERPRINT|PUBLIC|PUBKEY)", re.IGNORECASE)


def looks_secret(name: str, value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("${") and value.endswith("}"):
        return False  # a reference, resolved by the harness at launch
    if value.startswith(_SECRET_PREFIXES):
        return True
    if _PUBLIC_NAME.search(name):
        return False
    if _SECRET_NAME.search(name):
        return True
    # a long single token with mixed classes and no spaces reads as a credential
    if (
        len(value) >= 32
        and " " not in value
        and re.search(r"[A-Za-z]", value)
        and re.search(r"[0-9]", value)
    ):
        return True
    return False


class MCPServer(BaseModel):
    name: str
    harness: str
    scope: Literal["project", "user"]
    source: str  # str(path of the config file)
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env_names: list[str] = Field(default_factory=list)
    suspect_env: list[str] = Field(default_factory=list)
    config_hash: str = ""


def _entry_to_server(
    name: str, entry: dict, harness: str, scope: str, source: Path
) -> MCPServer:
    url = entry.get("url") or entry.get("serverUrl")
    env = entry.get("env") or {}
    env = env if isinstance(env, dict) else {}
    names = sorted(str(k) for k in env)
    suspect = sorted(k for k, v in env.items() if looks_secret(str(k), v))
    command = entry.get("command")
    args = [str(a) for a in entry.get("args") or []]
    normalized = json.dumps(
        {
            "name": name, "transport": "http" if url else "stdio",
            "command": command, "args": args, "url": url, "env_names": names,
        },
        sort_keys=True,
    )
    return MCPServer(
        name=name, harness=harness, scope=scope, source=str(source),
        transport="http" if url else "stdio",
        command=command, args=args, url=url,
        env_names=names, suspect_env=suspect,
        config_hash=hashlib.sha256(normalized.encode()).hexdigest(),
    )


def _servers_from_map(
    data: dict, harness: str, scope: str, source: Path
) -> list[MCPServer]:
    out = []
    for name, entry in data.items():
        if isinstance(entry, dict):
            out.append(_entry_to_server(str(name), entry, harness, scope, source))
    return out


def parse_config(
    path: Path, fmt: str, harness: str, scope: str, project_root: Path
) -> tuple[list[MCPServer], list[str]]:
    try:
        text = path.read_text()
        if fmt == "codex-toml":
            data = tomllib.loads(text)
            table = data.get("mcp_servers") or {}
            return _servers_from_map(table, harness, scope, path), []
        data = json.loads(text)
    except Exception as e:
        return [], [f"{path}: {type(e).__name__}: {e}"]
    if not isinstance(data, dict):
        return [], [f"{path}: expected a JSON object"]
    if fmt == "mcp-json":
        return _servers_from_map(data.get("mcpServers") or {}, harness, scope, path), []
    if fmt == "vscode-json":
        return _servers_from_map(data.get("servers") or {}, harness, scope, path), []
    if fmt == "claude-user-json":
        out = _servers_from_map(data.get("mcpServers") or {}, harness, "user", path)
        projects = data.get("projects") or {}
        proj_entry = projects.get(str(project_root.resolve())) or {}
        out += _servers_from_map(
            proj_entry.get("mcpServers") or {}, harness, "project", path
        )
        return out, []
    return [], [f"{path}: unknown MCP config format '{fmt}'"]


def discover_servers(
    harnesses: dict, project_root: Path, home: Path, global_only: bool = False
) -> tuple[list[MCPServer], list[tuple[str, str]]]:
    servers: list[MCPServer] = []
    errors: list[tuple[str, str]] = []
    for hid, h in sorted(harnesses.items()):
        sources = []
        if not global_only:
            sources += [
                (project_root / s, "project", h.mcp_format)
                for s in h.mcp_project_configs
            ]
        gfmt = h.mcp_format_global or h.mcp_format
        sources += [
            (home / s.removeprefix("~/"), "user", gfmt) for s in h.mcp_global_configs
        ]
        for path, scope, fmt in sources:
            if not path.is_file():
                continue
            found, errs = parse_config(path, fmt, hid, scope, project_root)
            servers += found
            errors += [(hid, e) for e in errs]
    return servers, errors
