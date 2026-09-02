from __future__ import annotations

from pathlib import Path

from drskill import deep, mcp
from drskill.checks import run_all
from drskill.checks.lockfile import load_lockfile
from drskill.discovery import discover
from drskill.harnesses import detect_harnesses, load_harnesses
from drskill.ledger import Config, load_effective_config
from drskill.models import Finding, Provenance
from drskill.resolution import World, build_world


def _add_tool_contributors(world: World, snapshots) -> None:
    import hashlib

    from drskill.models import Contributor, Deployment, TokenCost

    by_hash: dict[str, list] = {}
    for s in world.mcp_servers:
        by_hash.setdefault(s.config_hash, []).append(s)
    for cfg, snap in snapshots.items():
        servers = by_hash.get(cfg)
        if not servers:
            continue  # stale snapshot: no current server
        world.mcp_snapshot_dates[cfg] = snap.date
        world.mcp_snapshots[cfg] = snap
        deployments = [
            Deployment(harness=s.harness, path=s.source, scope=s.scope,
                       via_symlink=False, order=1_000_000)
            for s in servers
        ]
        for t in snap.tools:
            cid = f"{cfg}:{t.name}"
            # What the model actually sees per tool: name + description +
            # the input schema. Approximate name/description at ~4 chars per
            # token, matching the schema-token estimate from the handshake.
            text_tokens = max(0, (len(t.name) + len(t.description)) // 4)
            world.contributors[cid] = Contributor(
                id=cid, name=t.name, kind="mcp_tool",
                scope=servers[0].scope, routing_text=t.description,
                token_cost=TokenCost(
                    catalog_tokens=t.schema_tokens + text_tokens, body_tokens=0
                ),
                content_hash="sha256:"
                + hashlib.sha256(f"{t.name}\n{t.description}".encode()).hexdigest(),
                deployments=deployments,
            )


def run_scan(
    project_root: Path,
    home: Path,
    global_only: bool = False,
    config: Config | None = None,
    harness: str | None = None,
    judge: deep.JudgeFn | None = None,
    max_calls: int | None = 25,
    rewriter: deep.RewriteFn | None = None,
    mcp_connect: bool = False,
    progress=None,
) -> tuple[World, list[Finding]]:
    if config is None:
        # Same merge the CLI uses: machine-level acks are honored everywhere.
        config = load_effective_config(project_root, home, global_only)
    if progress:
        progress("discovering skills")
    if harness is None:
        harnesses = detect_harnesses(project_root, home, global_only)
    else:
        harnesses = [h for h in load_harnesses() if h.id == harness]
    instances, broken = [], []
    unreadable_states: list[tuple[str, str]] = []
    for h in harnesses:
        i, b, u = discover(h, project_root, home, global_only)
        instances += i
        broken += b
        unreadable_states += u
    world = build_world(instances, {h.id: h for h in harnesses}, broken)
    world.unreadable += [p for p in unreadable_states if p not in world.unreadable]
    world.lockfile = load_lockfile(project_root)
    if world.lockfile:
        for c in world.contributors.values():
            if c.source.kind in ("unmanaged", "linked") and c.name in world.lockfile:
                entry = world.lockfile[c.name]
                world.contributors[c.id] = c.model_copy(
                    update={
                        "source": Provenance(
                            kind="skills-lock", source=entry.get("source"),
                            path=_lock_skill_dir(entry.get("skillPath"))
                        )
                    }
                )
    if progress:
        progress("reading MCP configs")
    world.mcp_servers, world.mcp_config_errors = mcp.discover_servers(
        world.harnesses, project_root, home, global_only
    )
    from drskill import mcp_connect as mcpc

    sdir = mcpc.snapshot_dir(project_root, home, global_only)
    if mcp_connect:
        _, world.mcp_connect_failures = mcpc.run_handshakes(
            world.mcp_servers, sdir, progress=progress
        )
    _add_tool_contributors(world, mcpc.load_snapshots(sdir))
    world.mcp_approved = mcpc.load_snapshots(mcpc.approved_dir(sdir))
    from drskill.checks import skill_shell

    baselines = skill_shell.load_baselines(
        skill_shell.shell_dir(project_root, home, global_only)
    )
    for c in world.contributors.values():
        if c.kind != "skill":
            continue
        b = baselines.get(skill_shell.baseline_key(c, project_root, home))
        if b is not None:
            world.shell_approved[c.id] = b
    findings = run_all(world, config, progress=progress)
    cdir = deep.cache_dir(project_root, home, global_only)
    cache = deep.load_cache(cdir)
    acked_fps = {a.fingerprint for a in config.ack}
    if judge is not None:
        # Acked clusters never spend the call budget; the user already ruled.
        active = [f for f in findings if f.fingerprint not in acked_fps]
        deep.judge_pairs(
            world, active, cache, cdir, judge, config.deep.model, max_calls,
            rewriter=rewriter, progress=progress,
        )
    findings = deep.apply_verdicts(world, findings, cache, acked_fps)
    return world, findings


def _lock_skill_dir(skill_path) -> str | None:
    """skillPath points at SKILL.md; provenance records its directory."""
    if not isinstance(skill_path, str):
        return None
    return skill_path.removesuffix("SKILL.md").rstrip("/")
