"""Tier 2 heuristic checks: deterministic, threshold-tuned, always ack-able."""

from __future__ import annotations

import re
from itertools import combinations
from pathlib import Path

from drskill import text
from drskill.checks import check, make_finding
from drskill.checks.duplicates import estimate, shingles, signature
from drskill.ledger import Config
from drskill.models import Contributor, Finding
from drskill.resolution import World


def _skill_md(world: World) -> list[Contributor]:
    return [
        c
        for c in world.contributors.values()
        if Path(c.id).name == "SKILL.md" and c.frontmatter_valid
    ]


@check("missing-activation")
def missing_activation(world: World, config: Config) -> list[Finding]:
    return [
        make_finding(
            "missing-activation", "warning", [c],
            f"'{c.name}' never says when to use it; the router has to guess",
            fix_commands=[
                f"Start the description in {c.id} with a condition, e.g. 'Use when ...'"
            ],
        )
        for c in _skill_md(world)
        if c.routing_text.strip() and not text.has_activation(c.routing_text)
    ]


_IMPERATIVE = re.compile(r"\b(always|never)\s+((?:\w+[ \t]){0,3}\w+)", re.IGNORECASE)


def _imperative_phrases(c: Contributor) -> dict[str, list[set[str]]]:
    out: dict[str, list[set[str]]] = {"always": [], "never": []}
    for m in _IMPERATIVE.finditer(c.body):
        norm = {t for t in text.tokenize(m.group(2)) if t not in text.STOPWORDS}
        if norm:
            out[m.group(1).lower()].append(norm)
    return out


@check("opposing-imperatives")
def opposing_imperatives(world: World, config: Config) -> list[Finding]:
    cs = _skill_md(world)
    phrases = {c.id: _imperative_phrases(c) for c in cs}
    out = []
    for a, b in combinations(cs, 2):
        seen: set[str] = set()
        for kind_a, kind_b in (("always", "never"), ("never", "always")):
            for sa in phrases[a.id][kind_a]:
                for sb in phrases[b.id][kind_b]:
                    common = sa & sb
                    if not common:
                        continue
                    phrase = " ".join(sorted(common))
                    if phrase in seen:
                        continue
                    seen.add(phrase)
                    out.append(
                        make_finding(
                            "opposing-imperatives", "warning", [a, b],
                            f"'{a.name}' and '{b.name}' give opposite orders about "
                            f"'{phrase}' (always vs never); an agent loading both gets "
                            "contradictory instructions (low-recall check: paraphrased "
                            "contradictions are not detected)",
                            fix_commands=[
                                "Align the two instructions, or scope each to its own condition"
                            ],
                            extra_key=phrase,
                        )
                    )
    return out


def _is_duplicate_pair(
    a: Contributor,
    b: Contributor,
    near_threshold: float,
    sigs: dict[str, list[int]],
) -> bool:
    if a.content_hash == b.content_hash:
        return True
    return estimate(sigs[a.id], sigs[b.id]) >= near_threshold


@check("description-overlap")
def description_overlap(world: World, config: Config) -> list[Finding]:
    cs = [c for c in _skill_md(world) if c.routing_text.strip()]
    vecs = {c.id: text.shingle_vector(c.routing_text) for c in cs}
    sigs = {c.id: signature(shingles(f"{c.routing_text}\n{c.body}")) for c in cs}
    parent = {c.id: c.id for c in cs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in combinations(cs, 2):
        if _is_duplicate_pair(a, b, config.thresholds.near_duplicate, sigs):
            continue
        if text.cosine(vecs[a.id], vecs[b.id]) >= config.thresholds.description_overlap:
            parent[find(a.id)] = find(b.id)

    clusters: dict[str, list[Contributor]] = {}
    for c in cs:
        clusters.setdefault(find(c.id), []).append(c)

    out = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda c: c.name)
        phrases = text.shared_phrases([m.routing_text for m in members])[:3]
        claim = f" all claim '{phrases[0]}'" if phrases else " have near-identical descriptions"
        names = ", ".join(m.name for m in members)
        out.append(
            make_finding(
                "description-overlap", "warning", members,
                f"{len(members)} skills ({names}){claim}; "
                "none states an exclusive condition, so routing between them is a coin flip",
                fix_commands=[
                    "Give each description an exclusive 'use when' condition the others lack"
                ],
            )
        )
    return out


@check("generic-description")
def generic_description(world: World, config: Config) -> list[Finding]:
    out = []
    for c in _skill_md(world):
        if not c.routing_text.strip():
            continue
        distinct = {
            t for t in text.content_tokens(c.routing_text)
            if t not in text.GENERIC_VOCAB
        }
        if len(distinct) < config.thresholds.generic_min_distinct_tokens:
            out.append(
                make_finding(
                    "generic-description", "warning", [c],
                    f"'{c.name}' description has no distinguishing words to route on",
                    fix_commands=[
                        f"Name the concrete inputs, outputs, or domain in {c.id}"
                    ],
                )
            )
    return out
