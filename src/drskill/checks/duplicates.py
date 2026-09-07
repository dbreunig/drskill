"""Exact and near duplicate detection. Similarity uses a bottom-k sketch:
each shingle is hashed once with zlib.crc32 (builtin str hash() is salted
per process) and the k smallest values stand in for the set. The Jaccard
estimate is the fraction of the union's k smallest hashes present in both
sketches, which is exact whenever a set has k or fewer shingles."""

from __future__ import annotations

import shlex
import zlib
from itertools import combinations

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Contributor, Finding
from drskill.resolution import World

SHINGLE_WORDS = 5
NUM_HASHES = 128


def shingles(text: str, k: int = SHINGLE_WORDS) -> set[str]:
    words = text.lower().split()
    if len(words) <= k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def signature(sh: set[str]) -> list[int]:
    return sorted({zlib.crc32(s.encode()) for s in sh})[:NUM_HASHES]


def estimate(a: list[int], b: list[int]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    union_smallest = sorted(set(a) | set(b))[:NUM_HASHES]
    both = set(a) & set(b)
    return sum(1 for h in union_smallest if h in both) / len(union_smallest)


def _text(c: Contributor) -> str:
    return f"{c.routing_text}\n{c.body}"


def _harnesses_loading_both(world: World, a: Contributor, b: Contributor) -> set[str]:
    both = set()
    for hid in world.harnesses:
        eff = {c.id for c in world.effective(hid)}
        if a.id in eff and b.id in eff:
            both.add(hid)
    return both


@check("exact-duplicate")
def exact_duplicate(world: World, config: Config) -> list[Finding]:
    by_hash: dict[str, list[Contributor]] = {}
    for c in world.contributors.values():
        if c.kind != "skill":
            continue
        by_hash.setdefault(c.content_hash, []).append(c)
    out = []
    for group in by_hash.values():
        if len(group) < 2:
            continue
        # pairs co-loaded by one harness belong to double-load, not here
        clean_pairs = [
            (a, b)
            for a, b in combinations(group, 2)
            if not _harnesses_loading_both(world, a, b)
        ]
        if clean_pairs:
            out.append(
                make_finding(
                    "exact-duplicate", "warning", group,
                    "identical skills installed in more than one place: "
                    + ", ".join(sorted(c.id for c in group)),
                    fix_commands=[
                        f"npx skills remove {shlex.quote(group[0].name)}"
                        "  # from the copies you don't want"
                    ],
                )
            )
    return out


@check("near-duplicate")
def near_duplicate(world: World, config: Config) -> list[Finding]:
    cs = [c for c in world.contributors.values() if c.kind == "skill"]
    sigs = {c.id: signature(shingles(_text(c))) for c in cs}
    out = []
    for a, b in combinations(cs, 2):
        if a.content_hash == b.content_hash:
            continue
        sim = estimate(sigs[a.id], sigs[b.id])
        if sim >= config.thresholds.near_duplicate:
            out.append(
                make_finding(
                    "near-duplicate", "warning", [a, b],
                    f"'{a.name}' and '{b.name}' are ~{sim:.0%} similar; "
                    "likely the same skill twice",
                    fix_commands=[
                        f"Compare and keep one: diff {shlex.quote(a.id)} {shlex.quote(b.id)}",
                        f"npx skills remove {shlex.quote(b.name)}",
                    ],
                )
            )
    return out
