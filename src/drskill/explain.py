"""Simulate routing for a query: score every effective description per
harness, rank, and verdict. This is drskill's own similarity model, not
any harness's real router — every rendered output must say so."""

from __future__ import annotations

from dataclasses import dataclass

from drskill import text
from drskill.models import Contributor

SCORE_FLOOR = 0.1
TOP_N = 5


@dataclass
class Row:
    score: float
    contributor: Contributor


@dataclass
class QueryJudgeResult:
    routed: str | None
    contested: bool
    rationale: str


@dataclass
class HarnessRanking:
    harness: str
    rows: list[Row]
    verdict: str  # none | contested | routes
    top_name: str | None


def rank(world, query: str, margin: float, harness: str | None = None) -> list[HarnessRanking]:
    out: list[HarnessRanking] = []
    for hid in sorted(world.harnesses):
        if harness is not None and hid != harness:
            continue
        rows = []
        for c in world.effective(hid):
            if c.kind == "command":
                continue  # commands are invoked explicitly, never routed
            score = text.routing_score(query, c.routing_text)
            if score > 0:
                rows.append(Row(score=score, contributor=c))
        rows.sort(key=lambda r: (-r.score, r.contributor.name))
        rows = rows[:TOP_N]
        if not rows or rows[0].score < SCORE_FLOOR:
            out.append(HarnessRanking(hid, [], "none", None))
            continue
        contested = len(rows) > 1 and rows[0].score - rows[1].score < margin
        out.append(HarnessRanking(hid, rows,
                                  "contested" if contested else "routes",
                                  rows[0].contributor.name))
    return out


def group_rankings(rankings: list[HarnessRanking]) -> list[tuple[list[str], HarnessRanking]]:
    """Harnesses whose whole ranked result is identical share one table."""
    grouped: list[tuple[list[str], HarnessRanking]] = []
    keys: dict[tuple, int] = {}
    for r in rankings:
        key = (r.verdict, tuple((row.contributor.name, round(row.score, 6)) for row in r.rows))
        if key in keys:
            grouped[keys[key]][0].append(r.harness)
        else:
            keys[key] = len(grouped)
            grouped.append(([r.harness], r))
    return grouped
