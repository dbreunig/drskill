from __future__ import annotations

from pathlib import Path

from drskill.checks import run_all
from drskill.checks.lockfile import load_lockfile
from drskill.discovery import discover
from drskill.harnesses import detect_harnesses, load_harnesses
from drskill.ledger import Config, ledger_path, load_config
from drskill.models import Finding, Provenance
from drskill.resolution import World, build_world


def run_scan(
    project_root: Path,
    home: Path,
    global_only: bool = False,
    config: Config | None = None,
    harness: str | None = None,
) -> tuple[World, list[Finding]]:
    if config is None:
        config = load_config(ledger_path(project_root, home, global_only))
    if harness is None:
        harnesses = detect_harnesses(project_root, home, global_only)
    else:
        harnesses = [h for h in load_harnesses() if h.id == harness]
    instances, broken = [], []
    for h in harnesses:
        i, b = discover(h, project_root, home, global_only)
        instances += i
        broken += b
    world = build_world(instances, {h.id: h for h in harnesses}, broken)
    world.lockfile = load_lockfile(project_root)
    if world.lockfile:
        for c in world.contributors.values():
            if c.source.kind in ("unmanaged", "linked") and c.name in world.lockfile:
                entry = world.lockfile[c.name]
                world.contributors[c.id] = c.model_copy(
                    update={
                        "source": Provenance(
                            kind="skills-lock", source=entry.get("source")
                        )
                    }
                )
    return world, run_all(world, config)
