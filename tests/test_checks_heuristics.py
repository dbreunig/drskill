from pathlib import Path

from drskill.checks import run_all
from drskill.discovery import discover
from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.resolution import build_world


def world_from(proj, home):
    h = next(x for x in load_harnesses() if x.id == "claude-code")
    i, b = discover(h, proj, home)
    return build_world(i, {h.id: h}, b)


def write(proj, name, description, body="body"):
    d = proj / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    )


def by_check(findings, check_id):
    return [f for f in findings if f.check_id == check_id]


def test_missing_activation_fires_and_spares(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "no-cond", "Formats source code files.")
    write(proj, "with-cond", "Use when the user asks to reformat code.")
    findings = run_all(world_from(proj, home), Config())
    hits = by_check(findings, "missing-activation")
    assert [f.contributor_names for f in hits] == [["no-cond"]]
    assert hits[0].severity == "warning"


def test_missing_activation_skips_empty_description(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "empty-desc", "")
    findings = run_all(world_from(proj, home), Config())
    assert by_check(findings, "missing-activation") == []
    assert by_check(findings, "spec-missing-description")  # Tier 1 owns this


def test_generic_description(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "vague", "Helps with various tasks.")
    write(proj, "specific", "Use when the user asks to rebase, squash, or bisect with git.")
    findings = run_all(world_from(proj, home), Config())
    hits = by_check(findings, "generic-description")
    assert [f.contributor_names for f in hits] == [["vague"]]


def test_generic_threshold_tunable(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "two-tokens", "Renders diagrams nicely.")  # renders, diagrams, nicely = 3
    cfg = Config()
    cfg.thresholds.generic_min_distinct_tokens = 5
    findings = run_all(world_from(proj, home), cfg)
    assert by_check(findings, "generic-description")
