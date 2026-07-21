from __future__ import annotations

import difflib
import shlex
from datetime import datetime
from pathlib import Path

import shlex

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Contributor, Finding
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
                        f"Remove or rename the shadowed copy: {shlex.quote(str(d.path))}",
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
            if c.kind != "skill":
                continue
            if d.shadowed_by is None:
                by_hash.setdefault(c.content_hash, {}).setdefault(c.id, (c, d))
        for loads in by_hash.values():
            if len(loads) < 2:
                continue
            contributors = [c for c, _ in loads.values()]
            paths = ", ".join(str(d.path) for _, d in loads.values())
            quoted_paths = ", ".join(shlex.quote(str(d.path)) for _, d in loads.values())
            display = world.harnesses[hid].display_name
            out.append(
                make_finding(
                    "double-load", "error", contributors,
                    f"{display} loads the same skill "
                    f"'{contributors[0].name}' {len(loads)} times: {paths}",
                    harnesses=[hid],
                    extra_key=hid,
                    fix_commands=[f"Remove all but one copy ({quoted_paths})"],
                )
            )
    return out


_TS = "%Y-%m-%d %H:%M"


def _mtime(cid: str) -> tuple[float, str]:
    try:
        stamp = Path(cid).stat().st_mtime
    except OSError:
        return (0.0, "unknown")
    return (stamp, datetime.fromtimestamp(stamp).strftime(_TS))


@check("diverged-copies")
def diverged_copies(world: World, config: Config) -> list[Finding]:
    by_name: dict[str, list[Contributor]] = {}
    for c in world.contributors.values():
        if Path(c.id).name == "SKILL.md":
            by_name.setdefault(c.name, []).append(c)
    out = []
    for name, group in sorted(by_name.items()):
        if len({c.content_hash for c in group}) < 2:
            continue
        coloaded = False
        for hid in world.harnesses:
            loaded = {c.id for c, _d in world.harness_loads(hid)}
            if len([c for c in group if c.id in loaded]) >= 2:
                coloaded = True  # name-shadow / double-load territory
                break
        if coloaded:
            continue
        stamped = sorted(
            ((_mtime(c.id), c) for c in group), key=lambda x: x[0][0], reverse=True
        )
        descs = {c.routing_text for c in group}
        facts = "descriptions identical" if len(descs) == 1 else "descriptions differ"
        if len(group) == 2:
            a, b = group[0], group[1]
            n_lines = len(
                [ln for ln in difflib.unified_diff(a.body.splitlines(), b.body.splitlines())]
            )
            facts += f", bodies differ ({n_lines} diff lines)"
        lines = [f"two copies of '{name}' have drifted apart: {facts}"
                 if len(group) == 2 else
                 f"{len(group)} copies of '{name}' have drifted apart: {facts}"]
        tied = len({raw for (raw, _ts), _c in stamped}) == 1
        if tied:
            labels = ["copy"] * len(stamped)
        else:
            labels = ["newest"] + ["older"] * (len(stamped) - 1)
        for label, ((_raw, ts), c) in zip(labels, stamped):
            lines.append(f"        {label}: {c.id} ({ts})")
        if tied:
            fixes = ["Timestamps tie; compare by hand before keeping one"]
            if len(stamped) == 2:
                fixes.append(
                    f"diff {shlex.quote(stamped[0][1].id)} {shlex.quote(stamped[1][1].id)}"
                )
        else:
            fixes = ["Keep the newest copy and symlink or delete the others"]
            if len(stamped) == 2:
                newest_dir = str(Path(stamped[0][1].id).parent)
                older_dir = str(Path(stamped[1][1].id).parent)
                fixes.append(
                    f"rm -r {shlex.quote(older_dir)} && "
                    f"ln -s {shlex.quote(newest_dir)} {shlex.quote(older_dir)}"
                )
        out.append(
            make_finding(
                "diverged-copies", "warning", group,
                "\n".join(lines),
                fix_commands=fixes,
            )
        )
    return out
