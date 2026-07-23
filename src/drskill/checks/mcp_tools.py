"""Checks over enumerated MCP tools: connect failures, cross-server name
collisions, and unreviewed tool sets (rug-pull detection)."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from drskill import mcp_connect, text
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


def unreviewed_fingerprint(snap) -> str:
    """The rug-pull fingerprint of a snapshot. Public because the ack path
    uses it to find which snapshot a finding approved (cli Task 5)."""
    return _fp(
        "mcp-tools-unreviewed",
        [snap.server, snap.config_hash, *mcp_connect.tool_fingerprint_base(snap)],
    )


@check("mcp-tools-unreviewed")
def tools_unreviewed(world: World, config: Config) -> list[Finding]:
    out = []
    servers_by_cfg: dict[str, list] = defaultdict(list)
    for s in world.mcp_servers:
        servers_by_cfg[s.config_hash].append(s)
    for cfg, snap in sorted(world.mcp_snapshots.items()):
        servers = servers_by_cfg.get(cfg)
        if not servers:
            continue
        server = servers[0]
        harnesses = sorted({s.harness for s in servers})
        lines = "".join(
            f"\n        {t.name}: {text.one_line(t.description)}"
            for t in sorted(snap.tools, key=lambda t: t.name)
        )
        date = snap.date
        n = len(snap.tools)
        fp = unreviewed_fingerprint(snap)
        old_fp = _fp(
            "mcp-tools-unreviewed",
            [snap.server, cfg, *mcp_connect.tool_description_base(snap)],
        )
        prior = [
            a for a in config.ack
            if a.check == "mcp-tools-unreviewed" and server.name in a.skills
        ]
        prior_fps = {a.fingerprint for a in prior}
        changed = bool(prior) and fp not in prior_fps
        if changed and old_fp in prior_fps:
            # The descriptions the user approved are unchanged; drskill
            # grew to fingerprint schema text. One re-ack extends the
            # baseline. Not a rug pull, must not fail CI.
            head = (
                f"server '{server.name}' ({', '.join(harnesses)}) is "
                f"unchanged, but drskill now also fingerprints tool schema "
                f"text. Re-ack once to extend your approved baseline "
                f"(seen {date}):"
            )
            severity = "note"
        elif changed:
            when = next((str(a.date) for a in prior if a.date), "earlier")
            head = (
                f"server '{server.name}' ({', '.join(harnesses)}) CHANGED its "
                f"tools since you approved them ({when}). A server that rewrites "
                f"a tool description after you trusted it is worth a look. "
                f"Re-ack once you have reviewed the current set (seen {date}):"
            )
            severity = "warning"
        else:
            head = (
                f"server '{server.name}' ({', '.join(harnesses)}) has "
                f"{n} tool{'s' if n != 1 else ''} drskill has not recorded yet "
                f"(seen {date}). Acking saves this set as your approved "
                f"baseline, so drskill can flag it if the server later changes "
                f"a tool's description:"
            )
            severity = "note"
        out.append(Finding(
            check_id="mcp-tools-unreviewed", severity=severity,
            contributors=sorted({s.source for s in servers}),
            contributor_names=[server.name],
            harnesses=harnesses,
            message=head + lines,
            fix_commands=[f"drskill ack mcp-tools-unreviewed {server.name}"],
            fingerprint=fp,
        ))
    return out
