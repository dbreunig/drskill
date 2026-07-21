"""Checks over enumerated MCP tools: connect failures, cross-server name
collisions, and unreviewed tool sets (rug-pull detection)."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from drskill.checks import check
from drskill.ledger import Config
from drskill.models import Contributor, Finding
from drskill.resolution import World


def _fp(check_id: str, parts: list[str]) -> str:
    payload = "|".join([check_id, *sorted(parts)])
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _tools(world: World) -> list[Contributor]:
    return [c for c in world.contributors.values() if c.kind == "mcp_tool"]


@check("mcp-connect-failed")
def connect_failed(world: World, config: Config) -> list[Finding]:
    out = []
    for name, harness, message in world.mcp_connect_failures:
        out.append(Finding(
            check_id="mcp-connect-failed", severity="warning",
            contributors=[f"mcp:{harness}:{name}"], contributor_names=[name],
            harnesses=[harness],
            message=f"could not connect to MCP server '{name}': {message}",
            fix_commands=[f"Check the '{name}' server config, then rerun --mcp-connect"],
            fingerprint=_fp("mcp-connect-failed", [harness, name, message]),
        ))
    return out


@check("mcp-tool-collision")
def tool_collision(world: World, config: Config) -> list[Finding]:
    out = []
    # tool name -> harness -> set of owning config hashes
    per_name: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for c in _tools(world):
        cfg = c.id.split(":", 1)[0]
        for d in c.deployments:
            per_name[c.name][d.harness].add(cfg)
    for tname, per_harness in sorted(per_name.items()):
        clashing = sorted(h for h, cfgs in per_harness.items() if len(cfgs) > 1)
        if not clashing:
            continue
        out.append(Finding(
            check_id="mcp-tool-collision", severity="warning",
            contributors=sorted(c.id for c in _tools(world) if c.name == tname),
            contributor_names=[tname], harnesses=clashing,
            message=(
                f"tool '{tname}' is exposed by more than one server in the same "
                f"set; which one the agent gets is client dependent"
            ),
            fix_commands=[f"Disable '{tname}' on all but one server"],
            fingerprint=_fp("mcp-tool-collision", [tname, *clashing]),
        ))
    return out


@check("mcp-tools-unreviewed")
def tools_unreviewed(world: World, config: Config) -> list[Finding]:
    out = []
    by_cfg: dict[str, list[Contributor]] = defaultdict(list)
    for c in _tools(world):
        by_cfg[c.id.split(":", 1)[0]].append(c)
    servers_by_cfg = {s.config_hash: s for s in world.mcp_servers}
    for cfg, tools in sorted(by_cfg.items()):
        server = servers_by_cfg.get(cfg)
        if server is None:
            continue
        pairs = sorted(f"{c.name}\n{c.routing_text}" for c in tools)
        lines = "".join(
            f"\n        {c.name}: {c.routing_text}"
            for c in sorted(tools, key=lambda c: c.name)
        )
        date = world.mcp_snapshot_dates.get(cfg, "unknown")
        out.append(Finding(
            check_id="mcp-tools-unreviewed", severity="warning",
            contributors=[server.source], contributor_names=[server.name],
            harnesses=[server.harness],
            message=(
                f"server '{server.name}' exposes {len(tools)} unreviewed "
                f"tool{'s' if len(tools) != 1 else ''} (as of {date}); ack to "
                f"approve this exact tool set{lines}"
            ),
            fix_commands=[f"drskill ack mcp-tools-unreviewed {server.name}"],
            fingerprint=_fp("mcp-tools-unreviewed", [server.name, cfg, *pairs]),
        ))
    return out
