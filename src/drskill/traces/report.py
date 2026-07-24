"""Aggregate invocation records and render the audit report and drill-down."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from pydantic import BaseModel
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from drskill.report import _sanitize
from drskill.text import one_line
from drskill.traces.model import Invocation
from drskill.traces.pipeline import AuditData

MIN_SPAN_DAYS = 7.0
HEURISTIC_LEGEND = "~ counted from SKILL.md reads, not an explicit invocation event"


class NameStats(BaseModel):
    kind: str
    name: str
    server: str | None = None
    count: int = 0  # main-thread uses
    sidechain: int = 0
    sessions: int = 0
    last_used: dt.datetime
    heuristic: bool = False


class Coverage(BaseModel):
    harness: str
    first: dt.datetime
    last: dt.datetime
    sessions: int
    invocations: int

    @property
    def span_days(self) -> float:
        span = (self.last - self.first).total_seconds() / 86400
        return max(span, MIN_SPAN_DAYS)


def _key(inv: Invocation) -> tuple[str, str, str | None]:
    return (inv.kind, inv.name, inv.server)


def aggregate(invocations: list[Invocation]) -> dict[str, list[NameStats]]:
    grouped: dict[str, dict[tuple, list[Invocation]]] = defaultdict(lambda: defaultdict(list))
    for inv in invocations:
        grouped[inv.harness][_key(inv)].append(inv)
    out: dict[str, list[NameStats]] = {}
    for harness, names in grouped.items():
        stats = []
        for (kind, name, server), invs in names.items():
            stats.append(NameStats(
                kind=kind, name=name, server=server,
                count=sum(1 for i in invs if not i.sidechain),
                sidechain=sum(1 for i in invs if i.sidechain),
                sessions=len({i.session_id for i in invs}),
                last_used=max(i.timestamp for i in invs),
                heuristic=any(i.detection != "explicit" for i in invs),
            ))
        stats.sort(key=lambda s: (-s.count, s.name))
        out[harness] = stats
    return out


def coverage(invocations: list[Invocation]) -> dict[str, Coverage]:
    grouped: dict[str, list[Invocation]] = defaultdict(list)
    for inv in invocations:
        grouped[inv.harness].append(inv)
    return {
        harness: Coverage(
            harness=harness,
            first=min(i.timestamp for i in invs),
            last=max(i.timestamp for i in invs),
            sessions=len({i.session_id for i in invs}),
            invocations=len(invs),
        )
        for harness, invs in grouped.items()
    }


def rollup(invocations: list[Invocation]) -> list[tuple[NameStats, float]]:
    per_harness = aggregate(invocations)
    cov = coverage(invocations)
    merged: dict[tuple, NameStats] = {}
    rates: dict[tuple, float] = defaultdict(float)
    for harness, stats in per_harness.items():
        weeks = cov[harness].span_days / 7.0
        for s in stats:
            key = (s.kind, s.name, s.server)
            rates[key] += s.count / weeks
            if key in merged:
                m = merged[key]
                merged[key] = m.model_copy(update={
                    "count": m.count + s.count,
                    "sidechain": m.sidechain + s.sidechain,
                    "sessions": m.sessions + s.sessions,
                    "last_used": max(m.last_used, s.last_used),
                    "heuristic": m.heuristic or s.heuristic,
                })
            else:
                merged[key] = s
    ranked = [(merged[k], rates[k]) for k in merged]
    ranked.sort(key=lambda pair: (-pair[1], pair[0].name))
    return ranked


def _clean(text: str) -> str:
    return escape(_sanitize(text))


def _get_via_text(inv: Invocation) -> str:
    """Generate the 'via' text based on detection type."""
    if inv.detection == "explicit":
        return "explicit tool call"
    elif inv.detection == "command-marker":
        return f"/{_clean(inv.name)} slash command"
    elif inv.detection == "skill-read":
        return "SKILL.md read"
    else:
        return "unknown"


def _uses_cell(s: NameStats) -> str:
    if s.sidechain:
        return f"{s.count} (+{s.sidechain} subagent)"
    return str(s.count)


def render_audit(console: Console, data: AuditData) -> None:
    per_harness = aggregate(data.invocations)
    cov = coverage(data.invocations)
    any_heuristic = False
    for harness in sorted(per_harness):
        c = cov[harness]
        console.print(
            f"\n[bold]{_clean(harness)}[/bold]  coverage: "
            f"{c.first.date().isoformat()} to {c.last.date().isoformat()} · "
            f"{c.sessions} session{'s' if c.sessions != 1 else ''} · "
            f"{c.invocations} invocation{'s' if c.invocations != 1 else ''}"
        )
        table = Table(show_edge=False, pad_edge=False)
        for col in ("name", "kind", "server", "uses", "share", "sessions", "last used"):
            table.add_column(col)
        total_main = sum(s.count for s in per_harness[harness]) or 1
        for s in per_harness[harness]:
            marker = " ~" if s.heuristic else ""
            any_heuristic = any_heuristic or s.heuristic
            table.add_row(
                _clean(s.name) + marker,
                s.kind.replace("mcp_tool", "tool"),
                _clean(s.server or ""),
                _uses_cell(s),
                f"{100 * s.count / total_main:.0f}%",
                str(s.sessions),
                s.last_used.date().isoformat(),
            )
        console.print(table)
    if any_heuristic:
        console.print(f"[dim]{HEURISTIC_LEGEND}[/dim]")
    if len(per_harness) > 1:
        spans = {h: cov[h].span_days for h in per_harness}
        if max(spans.values()) / min(spans.values()) > 2:
            parts = ", ".join(f"{_clean(h)} {spans[h]:.0f}d" for h in sorted(spans))
            console.print(
                f"[dim]windows differ ({parts}); ranks compare rates, "
                "not raw counts[/dim]"
            )
        console.print("\n[bold]all harnesses[/bold]")
        table = Table(show_edge=False, pad_edge=False)
        for col in ("name", "kind", "uses", "/wk", "last used"):
            table.add_column(col)
        for s, rate in rollup(data.invocations):
            marker = " ~" if s.heuristic else ""
            table.add_row(_clean(s.name) + marker,
                          s.kind.replace("mcp_tool", "tool"),
                          _uses_cell(s), f"{rate:.1f}",
                          s.last_used.date().isoformat())
        console.print(table)
    if not data.invocations:
        console.print("no skill or MCP tool invocations found in trace history")
    _footer(console, data)


def _footer(console: Console, data: AuditData) -> None:
    if data.unreadable:
        n = len(data.unreadable)
        console.print(
            f"[dim]{n} trace file{'s' if n != 1 else ''} unreadable "
            "(--json lists them)[/dim]"
        )
    for harness, n in sorted(data.drifted.items()):
        console.print(
            f"[dim]{_clean(harness)}: {n} session file{'s' if n != 1 else ''} "
            "held no recognized events[/dim]"
        )


def matches(inv: Invocation, name: str) -> bool:
    if inv.name == name:
        return True
    if ":" in name:
        server, _, tool = name.partition(":")
        return inv.kind == "mcp_tool" and inv.server == server and inv.name == tool
    return False


def render_drilldown(console: Console, name: str, data: AuditData) -> None:
    hits = [i for i in data.invocations if matches(i, name)]
    if not hits:
        console.print(f"no invocations of {_clean(name)} found")
        _footer(console, data)
        return
    groups: dict[tuple, list[Invocation]] = defaultdict(list)
    for inv in hits:
        groups[_key(inv)].append(inv)
    for (kind, gname, server), invs in sorted(groups.items()):
        label = f"{gname} ({kind.replace('mcp_tool', 'MCP tool')}"
        label += f", server {server})" if server else ")"
        by_harness = defaultdict(int)
        for i in invs:
            by_harness[i.harness] += 1
        counts = ", ".join(f"{_clean(h)} {n}" for h, n in sorted(by_harness.items()))
        console.print(f"\n[bold]{_clean(label)}[/bold]  {counts}")
        for inv in sorted(invs, key=lambda i: i.timestamp, reverse=True):
            when = inv.timestamp.strftime("%Y-%m-%d %H:%M")
            where = inv.project or "unknown project"
            side = "  [dim](subagent)[/dim]" if inv.sidechain else ""
            console.print(f"  {when}  {_clean(inv.harness)}  {_clean(where)}{side}")
            console.print(f"    [dim]via: {_get_via_text(inv)}[/dim]")
            if inv.query:
                if inv.query_source:
                    console.print(
                        f"    query ({inv.query_source}): {_clean(inv.query)}"
                    )
                else:
                    console.print(f"    query: {_clean(inv.query)}")
            if inv.reasoning:
                console.print(f"    reasoning: {_clean(one_line(inv.reasoning, 200))}")
            trace = (f"{inv.source_file}:{inv.source_line}"
                     if inv.source_line is not None else inv.source_file)
            console.print(f"    [dim]trace: {_clean(trace)}[/dim]")
    _footer(console, data)
