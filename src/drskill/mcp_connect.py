"""The MCP handshake: connect to configured servers, enumerate their tools,
and snapshot the results. Everything that speaks the protocol lives behind a
lazy `mcp` SDK import in `connect_server`. Snapshots are value-free JSON;
no env value or secret is ever written."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, Field

from drskill.mcp import MCPServer, raw_server_env


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


class ConnectUnavailableError(Exception):
    """The mcp SDK is not installed; message shown to the user as-is."""


class ConnectError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _approx_tokens(obj) -> int:
    # schema token cost, approximate: 1 token per ~4 chars of compact JSON
    return max(0, len(json.dumps(obj, separators=(",", ":"))) // 4)


async def _enumerate(server: MCPServer):
    from mcp import ClientSession

    if server.transport == "http":
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(server.url) as (read, write, *_):
            async with ClientSession(read, write) as s:
                await s.initialize()
                return (await s.list_tools()).tools
    else:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=server.command or "", args=server.args,
            env=raw_server_env(server) or None,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                return (await s.list_tools()).tools


def connect_server(server: MCPServer, timeout: float = 15.0) -> ServerSnapshot:
    try:
        import mcp  # noqa: F401
    except ImportError as e:
        raise ConnectUnavailableError(
            "--mcp-connect needs the connect extra: "
            "uv tool install drskill (or pip install 'drskill-core[connect]')"
        ) from e

    async def _run():
        return await asyncio.wait_for(_enumerate(server), timeout)

    try:
        tools = asyncio.run(_run())
    except ConnectUnavailableError:
        raise
    except (asyncio.TimeoutError, TimeoutError) as e:
        raise ConnectError(f"timed out after {timeout:.0f}s") from e
    except Exception as e:
        raise ConnectError(f"{type(e).__name__}: {e}") from e
    return ServerSnapshot(
        server=server.name, config_hash=server.config_hash,
        date=dt.date.today().isoformat(),
        tools=[
            ToolInfo(
                name=t.name, description=t.description or "",
                schema_tokens=_approx_tokens(getattr(t, "inputSchema", {}) or {}),
            )
            for t in tools
        ],
    )


def run_handshakes(
    servers: list[MCPServer], sdir: Path, timeout: float = 15.0
) -> tuple[int, list[tuple[str, str, str]]]:
    connected = 0
    failures: list[tuple[str, str, str]] = []
    for server in servers:
        try:
            snap = connect_server(server, timeout)
        except ConnectError as e:
            failures.append((server.name, server.harness, e.message))
            continue
        save_snapshot(sdir, snap)
        connected += 1
    return connected, failures
