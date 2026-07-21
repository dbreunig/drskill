"""The deep tier's pure logic: pair keys, the committed verdict cache, and
how cached verdicts reshape findings. Nothing here imports dspy; everything
that touches the LLM lives in deep_llm.py behind a lazy import."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable
from importlib import metadata
from itertools import combinations
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from drskill.models import Contributor, Finding

VerdictClass = Literal["distinct", "description_collision", "scope_overlap"]

try:
    PROGRAM_VERSION = metadata.version("drskill")
except metadata.PackageNotFoundError:
    PROGRAM_VERSION = "unknown"


class JudgeResult(BaseModel):
    verdict: VerdictClass
    rationale: str
    detail: str  # distinguisher when distinct, else a confusion example


class Verdict(BaseModel):
    verdict: VerdictClass
    rationale: str
    detail: str
    model: str
    program_version: str
    date: str  # ISO date of the judgment


JudgeFn = Callable[[Contributor, Contributor], "JudgeResult | None"]


def cache_dir(project_root: Path, home: Path, global_mode: bool) -> Path:
    base = home if global_mode else project_root
    return base / ".drskill" / "cache"


def pair_key(a: Contributor, b: Contributor) -> str:
    parts = sorted(f"{c.name}\n{c.routing_text}" for c in (a, b))
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def load_cache(cdir: Path) -> dict[str, Verdict]:
    out: dict[str, Verdict] = {}
    if not cdir.is_dir():
        return out
    for p in sorted(cdir.glob("*.json")):
        try:
            out[p.stem] = Verdict(**json.loads(p.read_text()))
        except Exception:  # a corrupt entry is skipped, never fatal
            continue
    return out


def save_verdict(cdir: Path, key: str, v: Verdict) -> None:
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / f"{key}.json").write_text(v.model_dump_json(indent=2) + "\n")


def flagged_pairs(world, findings: list[Finding]) -> list[tuple[Contributor, Contributor]]:
    """All unordered member pairs of each description-overlap cluster.
    Largest cluster first, then name order, so repeated budgeted runs make
    progress instead of rejudging a shifting prefix."""
    overlaps = sorted(
        (f for f in findings if f.check_id == "description-overlap"),
        key=lambda f: (-len(f.contributors), f.contributor_names),
    )
    pairs: list[tuple[Contributor, Contributor]] = []
    for f in overlaps:
        members = sorted(
            (world.contributors[cid] for cid in f.contributors if cid in world.contributors),
            key=lambda c: c.name,
        )
        pairs.extend(combinations(members, 2))
    return pairs


def unjudged_count(world, findings: list[Finding], cache: dict[str, Verdict]) -> int:
    return sum(1 for a, b in flagged_pairs(world, findings) if pair_key(a, b) not in cache)


def apply_verdicts(
    world, findings: list[Finding], cache: dict[str, Verdict], acked_fps: set[str]
) -> list[Finding]:
    """Reshape description-overlap findings with cached verdicts. With an
    empty cache this is the identity, so users who never run --deep see no
    change. A fully distinct cluster downgrades to a visible note unless a
    member has an active injection finding; a suspected skill does not get
    to talk its way out of an overlap warning."""
    if not cache:
        return findings
    injected: set[str] = set()
    for f in findings:
        if f.check_id.startswith("injection-") and f.fingerprint not in acked_fps:
            injected.update(f.contributors)
    out: list[Finding] = []
    for f in findings:
        if f.check_id != "description-overlap":
            out.append(f)
            continue
        members = sorted(
            (world.contributors[cid] for cid in f.contributors if cid in world.contributors),
            key=lambda c: c.name,
        )
        pairs = list(combinations(members, 2))
        judged = {
            (a.name, b.name): cache[pair_key(a, b)]
            for a, b in pairs
            if pair_key(a, b) in cache
        }
        if not judged:
            out.append(f)
            continue
        blocked = sorted({m.name for m in members if m.id in injected})
        all_distinct = len(judged) == len(pairs) and all(
            v.verdict == "distinct" for v in judged.values()
        )
        if all_distinct and not blocked:
            latest = max(judged.values(), key=lambda v: v.date)
            names = ", ".join(m.name for m in members)
            out.append(f.model_copy(update={
                "severity": "note",
                "message": (
                    f"overlap flagged ({names}); judged distinct by "
                    f"{latest.model}, {latest.date}"
                ),
                "fix_commands": [],
            }))
            continue
        lines = []
        for (an, bn), v in judged.items():
            if v.verdict == "distinct":
                lines.append(f"\n      deep: {an} vs {bn}: distinct; {v.rationale}")
            else:
                lines.append(
                    f"\n      deep: {an} vs {bn}: {v.verdict}; {v.rationale}; "
                    f"confusion example: '{v.detail}'"
                )
        missing = len(pairs) - len(judged)
        if missing:
            lines.append(f"\n      deep: {missing} of {len(pairs)} pairs unjudged")
        if all_distinct and blocked:
            lines.append(
                "\n      deep: judged distinct, but downgrade withheld: "
                f"active injection findings on {', '.join(blocked)}"
            )
        out.append(f.model_copy(update={"message": f.message + "".join(lines)}))
    return out
