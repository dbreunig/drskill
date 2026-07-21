"""The deep tier's pure logic: pair keys, the committed verdict cache, and
how cached verdicts reshape findings. Nothing here imports dspy; everything
that touches the LLM lives in deep_llm.py behind a lazy import."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from collections.abc import Callable
from importlib import metadata
from itertools import combinations
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from drskill.models import Contributor, Finding

VerdictClass = Literal["distinct", "description_collision", "scope_overlap"]

try:
    PROGRAM_VERSION = metadata.version("drskill-core")
except metadata.PackageNotFoundError:
    try:  # pre-rename installs published the code as `drskill`
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
    # Rewrite proposal, present only on description_collision entries and
    # only once the rewrite call has succeeded. 0.3.0 entries lack all three.
    rewrite_target: str | None = None
    rewrite_text: str | None = None
    rewrite_reason: str | None = None


class RewriteResult(BaseModel):
    target: str  # name of the skill whose description should change
    text: str  # the proposed description
    reason: str  # one sentence on why that skill was picked


JudgeFn = Callable[[Contributor, Contributor], "JudgeResult | None"]
RewriteFn = Callable[[Contributor, Contributor, str], "RewriteResult | None"]


def load_user_env(home: Path) -> list[str]:
    """Read ~/.drskill/env (KEY=value lines, the AWS credentials-file shape)
    into the process environment. Only variables the shell has not already
    set are loaded; the shell always wins. The file is global and user
    owned on purpose: drskill never writes it, and it never reads one from
    a project, because a scanned repo is untrusted content. Returns the
    names it loaded."""
    path = home / ".drskill" / "env"
    if not path.is_file():
        return []
    loaded: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if not name or name in os.environ:
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


def cache_dir(project_root: Path, home: Path, global_mode: bool) -> Path:
    base = home if global_mode else project_root
    return base / ".drskill" / "cache"


def pair_key(a: Contributor, b: Contributor) -> str:
    # Each field is hashed on its own before joining, so no character a
    # skill can put in its name or description can shift the field
    # boundary and collide two different pairs onto one key.
    parts = sorted(
        hashlib.sha256(c.name.encode()).hexdigest()
        + hashlib.sha256(c.routing_text.encode()).hexdigest()
        for c in (a, b)
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


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


def pending_rewrites(world, findings: list[Finding], cache: dict[str, Verdict]) -> int:
    """Collision verdicts still waiting for a rewrite proposal, because the
    budget ran out or the rewrite call failed."""
    out = 0
    for a, b in flagged_pairs(world, findings):
        v = cache.get(pair_key(a, b))
        if v and v.verdict == "description_collision" and not v.rewrite_text:
            out += 1
    return out


def _flat(s: str) -> str:
    """Model and skill text rendered into single evidence lines must stay a
    single line: an embedded newline would break the diff layout and could
    forge report lines such as a fake fix command."""
    return " ".join(s.split())


def judge_pairs(
    world,
    findings: list[Finding],
    cache: dict[str, Verdict],
    cdir: Path,
    judge: JudgeFn,
    model_id: str,
    max_calls: int | None,
    rewriter: RewriteFn | None = None,
    progress=None,
) -> tuple[int, int]:
    """Judge uncached flagged pairs under a hard call budget; None means no
    limit. A description_collision verdict immediately spends one more call
    on its rewrite proposal, and collision entries missing a rewrite from a
    failed earlier call are retried before any new pair is judged. Every
    result lands in `cache` and on disk as it arrives, so an interrupted
    run loses nothing. Returns (judged, remaining unjudged)."""
    keyed = [(pair_key(a, b), a, b) for a, b in flagged_pairs(world, findings)]
    calls = 0
    # Three consecutive failures mean a persistent problem, so that program
    # stops burning the budget. The counters are per program: a dead
    # rewriter must not stop a healthy judge from judging new pairs.
    judge_failures = 0
    rewrite_failures = 0

    def budget_left() -> bool:
        return max_calls is None or calls < max_calls

    def attempt_rewrite(key: str, a: Contributor, b: Contributor) -> None:
        nonlocal calls, rewrite_failures
        v = cache[key]
        calls += 1
        r = rewriter(a, b, v.detail)
        if r is None or not r.text.strip():
            # A blank proposal is a failure, never a cached rewrite.
            rewrite_failures += 1
            return
        rewrite_failures = 0
        v = v.model_copy(update={
            "rewrite_target": r.target,
            "rewrite_text": r.text,
            "rewrite_reason": r.reason,
        })
        cache[key] = v
        save_verdict(cdir, key, v)

    # Retry pass: collisions whose rewrite call failed on an earlier run.
    if rewriter is not None:
        for key, a, b in keyed:
            if not budget_left() or rewrite_failures >= 3:
                break
            v = cache.get(key)
            if v and v.verdict == "description_collision" and not v.rewrite_text:
                attempt_rewrite(key, a, b)

    todo = [(key, a, b) for key, a, b in keyed if key not in cache]
    judged = 0
    for key, a, b in todo:
        if not budget_left() or judge_failures >= 3:
            break
        if progress:
            progress(f"judging {a.name} vs {b.name}")
        calls += 1
        result = judge(a, b)
        if result is None:  # errored or unparseable call: never cached
            judge_failures += 1
            continue
        judge_failures = 0
        v = Verdict(
            **result.model_dump(),
            model=model_id,
            program_version=PROGRAM_VERSION,
            date=dt.date.today().isoformat(),
        )
        cache[key] = v
        save_verdict(cdir, key, v)
        judged += 1
        if (
            rewriter is not None
            and v.verdict == "description_collision"
            and budget_left()
            and rewrite_failures < 3
        ):
            attempt_rewrite(key, a, b)
    return judged, len(todo) - judged


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
        by_name = {m.name: m for m in members}
        lines = []
        extra_fixes = []
        for (an, bn), v in judged.items():
            if v.verdict == "distinct":
                lines.append(f"\n      deep: {an} vs {bn}: distinct; {_flat(v.rationale)}")
                continue
            lines.append(
                f"\n      deep: {an} vs {bn}: {v.verdict}; {_flat(v.rationale)}; "
                f"confusion example: '{_flat(v.detail)}'"
            )
            target = by_name.get(v.rewrite_target or "")
            if v.verdict == "description_collision" and v.rewrite_text and target:
                lines.append(
                    f"\n      deep: rewrite for {target.name} ({_flat(v.rewrite_reason or '')}):"
                    f"\n      - {_flat(target.routing_text)}"
                    f"\n      + {_flat(v.rewrite_text)}"
                )
                if target.kind == "mcp_tool":
                    where = (
                        f"the '{target.name}' tool's description on its MCP server"
                    )
                else:
                    where = f"{target.id} by hand"
                extra_fixes.append(
                    f"Review the proposed description above, then edit {where}"
                )
        missing = len(pairs) - len(judged)
        if missing:
            lines.append(f"\n      deep: {missing} of {len(pairs)} pairs unjudged")
        if all_distinct and blocked:
            lines.append(
                "\n      deep: judged distinct, but downgrade withheld: "
                f"active injection findings on {', '.join(blocked)}"
            )
        update: dict = {"message": f.message + "".join(lines)}
        if extra_fixes:
            update["fix_commands"] = [*f.fix_commands, *extra_fixes]
        out.append(f.model_copy(update=update))
    return out
