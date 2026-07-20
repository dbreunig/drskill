import shlex
from pathlib import Path

from drskill.checks import run_all
from drskill.checks.duplicates import estimate, shingles, signature
from drskill.discovery import discover
from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.resolution import build_world

PAYLOAD = "'; echo pwned; '"

BODY = (
    "Use this skill to produce formatted documentation for Python projects. "
    "Read the module docstrings, build an outline, then render markdown pages "
    "with cross references and a table of contents. "
) * 5


def world_from(proj, home, harness_ids=("claude-code",)):
    hs = [h for h in load_harnesses() if h.id in harness_ids]
    instances, broken = [], []
    for h in hs:
        i, b = discover(h, proj, home)
        instances += i
        broken += b
    return build_world(instances, {h.id: h for h in hs}, broken)


def write(root, folder, name, description, body):
    d = root / folder / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}")


def test_minhash_estimates_similarity():
    a = signature(shingles(BODY))
    b = signature(shingles(BODY.replace("Python", "Ruby")))
    c = signature(shingles("Completely different text about cooking pasta at home."))
    assert estimate(a, a) == 1.0
    assert estimate(a, b) > 0.6
    assert estimate(a, c) < 0.2


def test_signature_is_deterministic():
    sig = signature(shingles(BODY))
    assert sig == signature(shingles(BODY))  # stable within and across runs (crc32)


def test_near_duplicate_fires(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, ".claude/skills", "doc-writer", "Writes docs.", BODY)
    write(proj, ".claude/skills", "docs-helper", "Helps with docs.", BODY + "One extra sentence.")
    cfg = Config()
    cfg.thresholds.near_duplicate = 0.5  # the pair's true Jaccard is ~0.8
    findings = run_all(world_from(proj, home), cfg)
    near = [f for f in findings if f.check_id == "near-duplicate"]
    assert len(near) == 1
    assert set(near[0].contributor_names) == {"doc-writer", "docs-helper"}


def test_near_duplicate_respects_threshold(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, ".claude/skills", "doc-writer", "Writes docs.", BODY)
    write(proj, ".claude/skills", "docs-helper", "Helps with docs.", BODY + "One extra sentence.")
    cfg = Config()
    cfg.thresholds.near_duplicate = 0.999  # different content can't estimate this high
    findings = run_all(world_from(proj, home), cfg)
    assert [f for f in findings if f.check_id == "near-duplicate"] == []


def test_exact_duplicate_across_harnesses_only(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    # same content under two different harness dirs, no harness loads both
    write(proj, ".claude/skills", "copy-a", "Same skill.", BODY)
    write(proj, ".pi/skills", "copy-b", "Same skill.", BODY)
    # make both contributors' content identical (names differ only in fm)
    (proj / ".claude/skills/copy-a/SKILL.md").write_text(f"---\nname: same\ndescription: d\n---\n{BODY}")
    (proj / ".pi/skills/copy-b/SKILL.md").write_text(f"---\nname: same\ndescription: d\n---\n{BODY}")
    findings = run_all(world_from(proj, home, ("claude-code", "pi")), Config())
    exact = [f for f in findings if f.check_id == "exact-duplicate"]
    assert len(exact) == 1
    assert set(exact[0].harnesses) == {"claude-code", "pi"}


def test_exact_duplicate_fires_despite_coloaded_subgroup(tmp_path):
    # 3-way group: claude-code co-loads two copies (double-load territory),
    # but the pi copy is a genuine cross-harness duplicate and must surface
    proj, home = tmp_path / "p", tmp_path / "h"
    content = f"---\nname: dup\ndescription: d\n---\n{BODY}"
    for root in [proj / ".claude/skills/dup", home / ".claude/skills/dup", proj / ".pi/skills/dup"]:
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(content)
    findings = run_all(world_from(proj, home, ("claude-code", "pi")), Config())
    exact = [f for f in findings if f.check_id == "exact-duplicate"]
    assert len(exact) == 1


def test_exact_duplicate_skips_pure_same_harness_coload(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    content = f"---\nname: dup\ndescription: d\n---\n{BODY}"
    for root in [proj / ".claude/skills/dup", home / ".claude/skills/dup"]:
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(content)
    findings = run_all(world_from(proj, home), Config())
    assert [f for f in findings if f.check_id == "exact-duplicate"] == []


def test_exact_duplicate_fix_command_quotes_adversarial_name(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    # double-quoted YAML scalar so the payload's own single quotes survive
    # frontmatter parsing unmangled
    content = f'---\nname: "{PAYLOAD}"\ndescription: d\n---\n{BODY}'
    for rel in [".claude/skills/dup-a", ".pi/skills/dup-b"]:
        d = proj / rel
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(content)
    findings = run_all(world_from(proj, home, ("claude-code", "pi")), Config())
    exact = [f for f in findings if f.check_id == "exact-duplicate"]
    assert len(exact) == 1
    cmd = exact[0].fix_commands[0]
    assert shlex.quote(PAYLOAD) in cmd
    assert shlex.split(cmd.split("#")[0])[-1] == PAYLOAD


def test_near_duplicate_fix_command_quotes_adversarial_name(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, ".claude/skills", "a-writer", "Writes reports.", BODY)
    d = proj / ".claude" / "skills" / "b-writer"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f'---\nname: "{PAYLOAD}"\ndescription: Helps with reports.\n---\n'
        f"{BODY}One extra sentence.\n"
    )
    cfg = Config()
    cfg.thresholds.near_duplicate = 0.5
    findings = run_all(world_from(proj, home), cfg)
    near = [f for f in findings if f.check_id == "near-duplicate"]
    assert len(near) == 1
    for cmd in near[0].fix_commands:
        assert shlex.quote(PAYLOAD) in cmd or PAYLOAD not in cmd
    remove_cmd = next(c for c in near[0].fix_commands if c.startswith("npx skills remove"))
    assert shlex.split(remove_cmd)[-1] == PAYLOAD
