"""Tier 2 heuristic checks: deterministic, threshold-tuned, always ack-able."""

from __future__ import annotations

from pathlib import Path

from drskill import text
from drskill.checks import check, make_finding
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
