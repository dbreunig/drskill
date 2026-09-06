"""Write MCP server entries into harness config files.

Only the shared mcp-json format is writable. claude-user-json is Claude
Code's whole user state file and codex-toml has no stdlib writer, so
callers print a paste-ready block for those formats instead of editing
them. Env variables are written with empty values; the manifest never
carries values, only names."""

from __future__ import annotations

import json
from pathlib import Path

from drskill.mcp import MCPServer, parse_config


class WriteUnsupportedError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def read_servers(path: Path, fmt: str) -> list[MCPServer]:
    if not path.is_file():
        return []
    servers, _errors = parse_config(path, fmt, harness="", scope="project",
                                    project_root=path.parent)
    return servers


def server_block(metadata: dict) -> dict:
    if metadata.get("transport") == "http":
        return {"url": metadata.get("url")}
    block: dict = {"command": metadata.get("command"), "args": list(metadata.get("args") or [])}
    env_names = metadata.get("env_names") or []
    if env_names:
        block["env"] = {name: "" for name in env_names}
    return block


def write_server(path: Path, name: str, block: dict, fmt: str = "mcp-json") -> None:
    if fmt != "mcp-json":
        raise WriteUnsupportedError(f"{fmt} config files are not writable; add the server by hand")
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise WriteUnsupportedError(f"could not read {path}: {e}")
        if not isinstance(data, dict):
            raise WriteUnsupportedError(f"{path} is not a JSON object")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise WriteUnsupportedError(f"{path} has a non-object mcpServers key")
    servers[name] = block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
