from __future__ import annotations

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World


@check("name-shadow")
def name_shadow(world: World, config: Config) -> list[Finding]:
    out = []
    for hid in world.harnesses:
        for c, d in world.harness_loads(hid):
            if d.shadowed_by is None:
                continue
            winner = world.contributors[d.shadowed_by]
            wdep = next(x for x in winner.deployments if x.harness == hid)
            out.append(
                make_finding(
                    "name-shadow", "warning", [winner, c],
                    f"two skills named '{c.name}': the {wdep.scope} copy at "
                    f"{wdep.path} wins by search order and shadows {d.path}",
                    harnesses=[hid],
                    extra_key=winner.id,
                    fix_commands=[
                        f"Remove or rename the shadowed copy: {d.path}",
                    ],
                )
            )
    return out


@check("double-load")
def double_load(world: World, config: Config) -> list[Finding]:
    out = []
    for hid in world.harnesses:
        # Key by content hash, then by contributor id, so that a single
        # contributor reached twice through the same real path (e.g. one
        # search directory symlinked into another) collapses to one entry
        # instead of looking like two distinct copies.
        by_hash: dict[str, dict[str, tuple]] = {}
        for c, d in world.harness_loads(hid):
            if d.shadowed_by is None:
                by_hash.setdefault(c.content_hash, {}).setdefault(c.id, (c, d))
        for loads in by_hash.values():
            if len(loads) < 2:
                continue
            contributors = [c for c, _ in loads.values()]
            paths = ", ".join(str(d.path) for _, d in loads.values())
            display = world.harnesses[hid].display_name
            out.append(
                make_finding(
                    "double-load", "error", contributors,
                    f"{display} loads the same skill "
                    f"'{contributors[0].name}' {len(loads)} times: {paths}",
                    harnesses=[hid],
                    extra_key=hid,
                    fix_commands=[f"Remove all but one copy ({paths})"],
                )
            )
    return out
