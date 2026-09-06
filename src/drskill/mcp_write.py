"""Write MCP server entries into harness config files.

mcp-json is read-write. codex-toml is append-only for stdio servers; the
append-only design preserves comments and formatting when adding servers.
claude-user-json is Claude Code's whole user state file and stays manual
because it holds Claude Code's complete user state. Env variables are
written with empty values; the manifest never carries values, only names."""

from __future__ import annotations

import json
import re
import tomllib
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


def validate_metadata(metadata) -> str | None:
    """One plain error string for a malformed entry, None when usable.
    Manifests are remote input; a bad shape must fail one entry with a
    message, never crash the whole command."""
    if not isinstance(metadata, dict):
        return "metadata is not an object"
    if metadata.get("transport") not in ("stdio", "http"):
        return f"unknown transport {metadata.get('transport')!r}"
    if metadata.get("command") is not None and not isinstance(metadata.get("command"), str):
        return "command is not a string"
    if metadata.get("url") is not None and not isinstance(metadata.get("url"), str):
        return "url is not a string"
    if metadata.get("server_name") is not None and not isinstance(metadata.get("server_name"), str):
        return "server_name is not a string"
    args = metadata.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return "args is not a list of strings"
    env_names = metadata.get("env_names") or []
    if not isinstance(env_names, list) or not all(isinstance(n, str) for n in env_names):
        return "env_names is not a list of strings"
    return None


def server_block(metadata: dict) -> dict:
    env_names = metadata.get("env_names") or []
    if metadata.get("transport") == "http":
        block: dict = {"url": metadata.get("url")}
        if env_names:
            block["env"] = {name: "" for name in env_names}
        return block
    block = {"command": metadata.get("command"), "args": list(metadata.get("args") or [])}
    if env_names:
        block["env"] = {name: "" for name in env_names}
    return block


def write_server(path: Path, name: str, block: dict, fmt: str = "mcp-json",
                 replace: bool = False) -> None:
    if fmt == "codex-toml":
        _write_codex_server(path, name, block)
        return
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
    if name in servers and not replace:
        # A same-named entry the parser skipped (a non-dict value) is
        # invisible to the caller's drift check; refuse rather than
        # silently overwrite it.
        raise WriteUnsupportedError(
            f"{path} already has an entry named {name!r} that drskill "
            "cannot parse; edit it by hand")
    servers[name] = block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _toml_str(value: str) -> str:
    # JSON string escaping is a valid TOML basic string, so json.dumps
    # doubles as the TOML serializer for scalars.
    return json.dumps(value)


def _write_codex_server(path: Path, name: str, block: dict) -> None:
    """Append one [mcp_servers.<name>] table. Append-only: replacing a
    table in place would mean rewriting the user's TOML around comments,
    so a same-named server always refuses, --force included."""
    if "url" in block:
        raise WriteUnsupportedError(
            "codex-toml supports stdio servers only; add http servers by hand")
    text = ""
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
            existing = tomllib.loads(text).get("mcp_servers") or {}
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise WriteUnsupportedError(f"could not read {path}: {e}")
        if name in existing:
            raise WriteUnsupportedError(
                f"{path} already has [mcp_servers.{name}]; edit it by hand")
    key = name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _toml_str(name)
    lines = [f"[mcp_servers.{key}]",
             f"command = {_toml_str(block.get('command') or '')}",
             "args = [" + ", ".join(_toml_str(a) for a in block.get("args") or []) + "]"]
    env = block.get("env") or {}
    if env:
        lines.append("env = {" + ", ".join(f'{_toml_str(k)} = ""' for k in env) + "}")
    prefix = text if not text or text.endswith("\n") else text + "\n"
    if prefix:
        prefix += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prefix + "\n".join(lines) + "\n", encoding="utf-8")
