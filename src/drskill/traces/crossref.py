"""Join the scanned world against trace history: what is installed but
never invoked. Reporting-only, like the rest of audit — no findings, no
acks, no CI effect. Guards keep fresh installs and young trace windows
from reading as staleness."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from drskill.traces.model import Invocation

_KIND_LABEL = {"skill": "skill", "command": "command", "mcp_tool": "mcp tool"}


@dataclass
class Unused:
    kind: str
    name: str
    where: str


def _covered_harnesses(invocations: list[Invocation], unused_days: int,
                       today: dt.date) -> set[str]:
    earliest: dict[str, dt.date] = {}
    for inv in invocations:
        d = inv.timestamp.date()
        if inv.harness not in earliest or d < earliest[inv.harness]:
            earliest[inv.harness] = d
    return {h for h, d in earliest.items() if (today - d).days >= unused_days}


def unused_contributors(world, invocations: list[Invocation], pins: dict,
                        unused_days: int, today: dt.date) -> list[Unused] | None:
    covered = _covered_harnesses(invocations, unused_days, today)
    if not covered:
        return None

    used_names: set[str] = set()
    used_tools: set[tuple[str, str]] = set()
    for inv in invocations:
        if inv.kind == "mcp_tool":
            used_tools.add((inv.server or "", inv.name))
        else:
            used_names.add(inv.name)

    server_by_cfg = {s.config_hash: s.name for s in world.mcp_servers}
    out: list[Unused] = []
    for c in world.contributors.values():
        if c.system:
            continue
        harnesses = sorted({d.harness for d in c.deployments})
        if not any(h in covered for h in harnesses):
            continue  # unknown, not unused: no covered trace window applies
        pin = pins.get(c.id)
        if pin is not None and pin.installed_at:
            try:
                installed = dt.date.fromisoformat(pin.installed_at)
            except ValueError:
                installed = None
            if installed is not None and (today - installed).days < unused_days:
                continue  # a fresh install has not had time to be used
        if c.kind == "mcp_tool":
            server = server_by_cfg.get(c.id.split(":", 1)[0])
            if server is None:
                continue  # unresolvable server: unknown, not unused
            if (server, c.name) in used_tools:
                continue
            out.append(Unused(_KIND_LABEL[c.kind], c.name, server))
        else:
            names = {c.name}
            if c.suite:
                names.add(f"{c.suite}:{c.name}")
            if names & used_names:
                continue
            out.append(Unused(_KIND_LABEL[c.kind], c.name, ", ".join(harnesses)))
    out.sort(key=lambda u: (u.kind, u.name))
    return out
