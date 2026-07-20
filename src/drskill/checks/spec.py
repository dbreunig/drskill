"""Spec-compliance checks. Only SKILL.md contributors are checked; bare
.md skills (pi root files) are not SKILL.md spec artifacts."""

from __future__ import annotations

import shlex
from pathlib import Path

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World

DESCRIPTION_MAX = 1024


def _skill_md_contributors(world: World):
    return [c for c in world.contributors.values() if Path(c.id).name == "SKILL.md"]


@check("spec-invalid-frontmatter")
def spec_invalid_frontmatter(world: World, config: Config) -> list[Finding]:
    return [
        make_finding(
            "spec-invalid-frontmatter", "error", [c],
            f"'{c.name}': frontmatter does not parse as YAML ({c.id})",
            fix_commands=[f"Fix the YAML frontmatter in {shlex.quote(c.id)}"],
        )
        for c in _skill_md_contributors(world)
        if not c.frontmatter_valid
    ]


@check("spec-name-mismatch")
def spec_name_mismatch(world: World, config: Config) -> list[Finding]:
    out = []
    for c in _skill_md_contributors(world):
        folder = Path(c.id).parent.name
        if c.frontmatter_valid and c.name != folder:
            out.append(
                make_finding(
                    "spec-name-mismatch", "error", [c],
                    f"frontmatter name '{c.name}' does not match folder '{folder}'",
                    fix_commands=[
                        f"Rename the folder to {shlex.quote(c.name)} or set "
                        f"'name: {shlex.quote(folder)}' in {shlex.quote(c.id)}"
                    ],
                )
            )
    return out


@check("spec-missing-description")
def spec_missing_description(world: World, config: Config) -> list[Finding]:
    return [
        make_finding(
            "spec-missing-description", "error", [c],
            f"'{c.name}' has no description; the router cannot route to it",
            fix_commands=[
                f"Add a 'description:' with a clear 'use when' condition to {shlex.quote(c.id)}"
            ],
        )
        for c in _skill_md_contributors(world)
        if c.frontmatter_valid and not c.routing_text.strip()
    ]


@check("spec-description-too-long")
def spec_description_too_long(world: World, config: Config) -> list[Finding]:
    return [
        make_finding(
            "spec-description-too-long", "error", [c],
            f"'{c.name}' description is {len(c.routing_text)} chars (max {DESCRIPTION_MAX})",
            fix_commands=[
                f"Shorten the description in {shlex.quote(c.id)} to {DESCRIPTION_MAX} chars or fewer"
            ],
        )
        for c in _skill_md_contributors(world)
        if len(c.routing_text) > DESCRIPTION_MAX
    ]


def _has_angle_bracket(value: object) -> bool:
    """Look for '<' or '>' in parsed frontmatter values, not the raw YAML
    text. Raw text also contains YAML's own syntax characters (e.g. the
    folded block scalar indicator in 'description: >-'), which are not
    frontmatter values and would false-positive on ordinary multi-line
    descriptions."""
    if isinstance(value, str):
        return "<" in value or ">" in value
    if isinstance(value, dict):
        return any(_has_angle_bracket(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_angle_bracket(v) for v in value)
    return False


@check("frontmatter-angle-brackets")
def frontmatter_angle_brackets(world: World, config: Config) -> list[Finding]:
    return [
        make_finding(
            "frontmatter-angle-brackets", "warning", [c],
            f"'{c.name}' frontmatter contains angle brackets, a spec-flagged injection vector",
            fix_commands=[f"Remove '<' and '>' from the frontmatter of {shlex.quote(c.id)}"],
        )
        for c in _skill_md_contributors(world)
        if c.frontmatter_valid and _has_angle_bracket(c.frontmatter)
    ]
