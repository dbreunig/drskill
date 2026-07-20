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
            f"'{c.name}' never says when to use it; the router has to guess "
            "(or a user has to invoke it by name)",
            fix_commands=[
                f"Start the description in {c.id} with a condition, e.g. 'Use when ...'"
            ],
            fingerprint_texts=[f"{c.name}\n{c.routing_text}"],
        )
        for c in _skill_md(world)
        if c.routing_text.strip() and not text.has_activation(c.routing_text)
    ]


_IMPERATIVE = re.compile(r"\b(always|never)\s+((?:\w+[ \t]){0,3}\w+)", re.IGNORECASE)

# Unlike text.STOPWORDS, verbs stay: "use tabs" needs "use". Only glue words
# and location/degree adverbs are dropped.
# Corpus tuning 2026-07-20: set-intersection matching fired 119 times and
# set-containment 248 times on the hermes corpus (179 skills), almost all
# single shared verbs. The shipped rule is strict verb+object bigram
# equality: the first two non-glue tokens after always/never must match
# exactly. Very low recall, near-zero noise.
_IMPERATIVE_DROP = frozenset(
    """a an the and or for in on at to of with from by anywhere everywhere
    nowhere here there always never all any this that these those it its
    your you""".split()
)


_SNIPPET_MAX = 100


def _snippet(body: str, start: int, end: int) -> str:
    line_start = body.rfind("\n", 0, start) + 1
    line_end = body.find("\n", end)
    if line_end == -1:
        line_end = len(body)
    line = body[line_start:line_end].strip()
    if len(line) > _SNIPPET_MAX:
        line = line[: _SNIPPET_MAX - 1].rstrip() + "…"
    return line


def _imperative_phrases(c: Contributor) -> dict[str, dict[tuple[str, str], str]]:
    """kind -> {verb+object bigram: snippet of the first line saying it}."""
    out: dict[str, dict[tuple[str, str], str]] = {"always": {}, "never": {}}
    for m in _IMPERATIVE.finditer(c.body):
        toks = [t for t in text.tokenize(m.group(2)) if t not in _IMPERATIVE_DROP]
        if len(toks) >= 2:
            out[m.group(1).lower()].setdefault(
                (toks[0], toks[1]), _snippet(c.body, m.start(), m.end())
            )
    return out


@check("opposing-imperatives")
def opposing_imperatives(world: World, config: Config) -> list[Finding]:
    cs = _skill_md(world)
    phrases = {c.id: _imperative_phrases(c) for c in cs}
    out = []
    for a, b in combinations(cs, 2):
        seen: set[str] = set()
        for kind_a, kind_b in (("always", "never"), ("never", "always")):
            for pair in sorted(set(phrases[a.id][kind_a]) & set(phrases[b.id][kind_b])):
                phrase = " ".join(pair)
                if phrase in seen:
                    continue
                seen.add(phrase)
                lines = [
                    f"'{a.name}' and '{b.name}' give opposite orders about "
                    f"'{phrase}':",
                    f'        {a.name}: "{phrases[a.id][kind_a][pair]}"',
                    f"            {a.id}",
                    f'        {b.name}: "{phrases[b.id][kind_b][pair]}"',
                    f"            {b.id}",
                    "        (exact-match check; paraphrased contradictions are"
                    " not detected)",
                ]
                out.append(
                    make_finding(
                        "opposing-imperatives", "warning", [a, b],
                        "\n".join(lines),
                        fix_commands=[
                            "Align the two instructions, or scope each to its own condition"
                        ],
                        extra_key=phrase,
                        fingerprint_texts=[f"{a.name}\n{a.body}", f"{b.name}\n{b.body}"],
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

    # Collapse duplicate groups to one representative each BEFORE clustering.
    # Skipping only the direct edge is not enough: a carved-out duplicate
    # pair could re-enter one cluster through a third skill that overlaps
    # both (found in review). Duplicates are the stronger diagnosis and are
    # reported by their own checks.
    rep_parent = {c.id: c.id for c in cs}

    def rep_find(x: str) -> str:
        while rep_parent[x] != x:
            rep_parent[x] = rep_parent[rep_parent[x]]
            x = rep_parent[x]
        return x

    for a, b in combinations(cs, 2):
        # Same-name pairs are diverged-copies territory; duplicate pairs are
        # exact/near-duplicate territory. Both collapse to one representative.
        if a.name == b.name or _is_duplicate_pair(
            a, b, config.thresholds.near_duplicate, sigs
        ):
            rep_parent[rep_find(a.id)] = rep_find(b.id)
    by_id = {c.id: c for c in cs}
    reps = [by_id[cid] for cid in sorted({rep_find(c.id) for c in cs})]

    parent = {c.id: c.id for c in reps}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in combinations(reps, 2):
        if text.cosine(vecs[a.id], vecs[b.id]) >= config.thresholds.description_overlap:
            parent[find(a.id)] = find(b.id)

    clusters: dict[str, list[Contributor]] = {}
    for c in reps:
        clusters.setdefault(find(c.id), []).append(c)

    out = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda c: c.name)
        phrases = text.shared_phrases([m.routing_text for m in members])[:3]
        if phrases:
            quoted = ", ".join(f"'{p}'" for p in phrases)
            claim = f" all claim {quoted}"
        else:
            claim = " have near-identical descriptions"
        # Same-name members collapse to one representative above, so names
        # inside a cluster are unique and need no path disambiguation.
        names = ", ".join(m.name for m in members)
        member_lines = "".join(f"\n        {m.name}: {m.id}" for m in members)
        out.append(
            make_finding(
                "description-overlap", "warning", members,
                f"{len(members)} skills ({names}){claim}; "
                "none states an exclusive condition, so routing between them "
                f"is a coin flip{member_lines}",
                fix_commands=[
                    "Give each description an exclusive 'use when' condition the others lack"
                ],
                fingerprint_texts=[f"{m.name}\n{m.routing_text}" for m in members],
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
                    fingerprint_texts=[f"{c.name}\n{c.routing_text}"],
                )
            )
    return out
