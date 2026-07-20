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


PILE_A = "Use when the user asks to write project documentation pages."
PILE_B = "Use when the user asks to write project documentation summaries."
PILE_C = "Use when the user asks to write project documentation chapters."


def test_overlap_cluster_fires_with_shared_phrases(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "doc-a", PILE_A, body="a" * 40)
    write(proj, "doc-b", PILE_B, body="b" * 40)
    write(proj, "doc-c", PILE_C, body="c" * 40)
    write(proj, "git-helper", "Use when the user asks to rebase with git.", body="g" * 40)
    findings = run_all(world_from(proj, home), Config())
    hits = by_check(findings, "description-overlap")
    assert len(hits) == 1
    assert set(hits[0].contributor_names) == {"doc-a", "doc-b", "doc-c"}
    assert "write project documentation" in hits[0].message
    assert "git-helper" not in hits[0].contributor_names


def test_overlap_excludes_duplicate_pairs(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    shared_body = "Collect the metrics and summarize each work stream carefully. " * 10
    write(proj, "dup-a", PILE_A, body=shared_body)
    write(proj, "dup-b", PILE_B, body=shared_body + "extra.")
    cfg = Config()
    cfg.thresholds.near_duplicate = 0.5  # make the pair a near-duplicate
    findings = run_all(world_from(proj, home), cfg)
    assert by_check(findings, "near-duplicate")
    assert by_check(findings, "description-overlap") == []


def test_overlap_threshold_tunable(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "doc-a", PILE_A, body="a" * 40)
    write(proj, "doc-b", PILE_B, body="b" * 40)
    cfg = Config()
    cfg.thresholds.description_overlap = 0.999
    findings = run_all(world_from(proj, home), cfg)
    assert by_check(findings, "description-overlap") == []


def test_opposing_imperatives(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "tabs", "Use when formatting code with tabs.",
          body="Always use tabs for indentation.")
    write(proj, "spaces", "Use when formatting code with spaces.",
          body="Never use tabs anywhere in the file.")
    write(proj, "meals", "Use when planning meals for the week.",
          body="Never skip breakfast before coding.")
    findings = run_all(world_from(proj, home), Config())
    hits = by_check(findings, "opposing-imperatives")
    assert len(hits) == 1
    assert set(hits[0].contributor_names) == {"tabs", "spaces"}
    assert "tabs" in hits[0].message


def test_opposing_near_miss_stays_quiet(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "a", "Use when doing a.", body="Always use tabs here.")
    write(proj, "b", "Use when doing b.", body="Never use spaces here.")
    findings = run_all(world_from(proj, home), Config())
    assert by_check(findings, "opposing-imperatives") == []


def test_overlap_disambiguates_colliding_names(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "docs", PILE_A, body="x" * 40)  # .claude/skills/docs
    d = proj / ".pi" / "skills" / "docs"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: docs\ndescription: {PILE_B}\n---\n{'y' * 40}\n")
    hs = [h for h in load_harnesses() if h.id in ("claude-code", "pi")]
    instances, broken = [], []
    for h in hs:
        i, b = discover(h, proj, home)
        instances += i
        broken += b
    world = build_world(instances, {h.id: h for h in hs}, broken)
    findings = run_all(world, Config())
    hits = by_check(findings, "description-overlap")
    assert len(hits) == 1
    assert "docs (.claude)" in hits[0].message
    assert "docs (.pi)" in hits[0].message
