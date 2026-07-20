import os
import shlex
from pathlib import Path

from drskill.checks import run_all
from drskill.discovery import discover
from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.resolution import build_world

PAYLOAD = "'; echo pwned; '"


def world_from(proj, home, harness_ids=("claude-code",)):
    hs = [h for h in load_harnesses() if h.id in harness_ids]
    instances, broken = [], []
    for h in hs:
        i, b = discover(h, proj, home)
        instances += i
        broken += b
    return build_world(instances, {h.id: h for h in hs}, broken)


def write(root, rel, name, body="body"):
    d = root / rel / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}\n")
    return d


def find(findings, check_id):
    return [f for f in findings if f.check_id == check_id]


def test_name_shadow(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, ".claude/skills", "tool", body="project version")
    write(home, ".claude/skills", "tool", body="user version")
    findings = run_all(world_from(proj, home), Config())
    shadows = find(findings, "name-shadow")
    assert len(shadows) == 1
    assert shadows[0].severity == "warning"
    assert "project" in shadows[0].message  # names the winner's scope


def test_double_load_two_real_copies(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    # the gh-skill x npx-skills interaction: two independently materialized
    # copies with identical content, both read by pi from different search
    # directories -- two distinct real paths, not one file reached twice.
    content = "---\nname: pdf-tools\ndescription: d\n---\nbody\n"
    for rel in [".agents/skills/pdf-tools", ".pi/skills/pdf-tools"]:
        d = proj / rel
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(content)
    findings = run_all(world_from(proj, home, ("pi",)), Config())
    dl = find(findings, "double-load")
    assert len(dl) == 1
    assert dl[0].severity == "error"
    assert dl[0].harnesses == ["pi"]


def test_no_double_load_when_symlink_resolves_to_same_real_path(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    # one search directory symlinked into another within the same harness's
    # own search paths (e.g. ~/.pi/agent/skills/x -> ~/.agents/skills/x):
    # both instances resolve to the same real file, so this is one
    # contributor with two deployments, not a double-load.
    canonical = write(proj, ".agents/skills", "pdf-tools")
    d = proj / ".pi" / "skills"
    d.mkdir(parents=True)
    os.symlink(canonical, d / "pdf-tools")
    findings = run_all(world_from(proj, home, ("pi",)), Config())
    assert find(findings, "double-load") == []


def test_no_double_load_when_shadowed(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, ".pi/skills", "tool", body="pi version")
    write(proj, ".agents/skills", "tool", body="different version")
    findings = run_all(world_from(proj, home, ("pi",)), Config())
    assert find(findings, "double-load") == []
    assert len(find(findings, "name-shadow")) == 1


def test_name_shadow_conflicting_winners_stay_distinct(tmp_path):
    # pi is global-first, gemini-cli is project-first; both read .agents/skills
    # (project) and ~/.agents/skills (user). The two harnesses disagree on
    # which copy wins, so merging their findings into one (as a
    # content-hash-only fingerprint would) would report the wrong winner for
    # whichever harness lost the merge.
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, ".agents/skills", "tool", body="project version")
    write(home, ".agents/skills", "tool", body="user version")
    findings = run_all(world_from(proj, home, ("pi", "gemini-cli")), Config())
    shadows = find(findings, "name-shadow")
    assert len(shadows) == 2
    pi_finding = next(f for f in shadows if "pi" in f.harnesses)
    gemini_finding = next(f for f in shadows if "gemini-cli" in f.harnesses)
    assert pi_finding.fingerprint != gemini_finding.fingerprint
    # pi: global-first -> the user/global copy wins
    assert "user" in pi_finding.message
    # gemini-cli: project-first -> the project copy wins
    assert "project" in gemini_finding.message


def test_broken_symlink(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    d = proj / ".claude" / "skills"
    d.mkdir(parents=True)
    os.symlink(proj / "gone", d / "dead")
    findings = run_all(world_from(proj, home), Config())
    bs = find(findings, "broken-symlink")
    assert len(bs) == 1 and bs[0].severity == "error"
    assert "dead" in bs[0].message


def test_broken_symlink_fix_command_quotes_adversarial_path(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    d = proj / ".claude" / "skills"
    d.mkdir(parents=True)
    os.symlink(proj / "gone", d / PAYLOAD)
    findings = run_all(world_from(proj, home), Config())
    bs = find(findings, "broken-symlink")
    assert len(bs) == 1
    cmd = bs[0].fix_commands[0]
    assert shlex.split(cmd)[-1].endswith(PAYLOAD)


def test_name_shadow_fix_command_quotes_adversarial_name(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, ".claude/skills", PAYLOAD, body="project version")
    write(home, ".claude/skills", PAYLOAD, body="user version")
    findings = run_all(world_from(proj, home), Config())
    shadows = find(findings, "name-shadow")
    assert len(shadows) == 1
    cmd = shadows[0].fix_commands[0]
    assert PAYLOAD in shlex.split(cmd)[-1]


def _write_named(root, rel, name, body):
    d = root / rel / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}\n")


def test_diverged_copies_fires_across_harnesses(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    _write_named(proj, ".claude/skills", "geo", "new body with extra sections\n" * 3)
    _write_named(proj, ".pi/skills", "geo", "old body\n")
    findings = run_all(world_from(proj, home, ("claude-code", "pi")), Config())
    hits = find(findings, "diverged-copies")
    assert len(hits) == 1
    msg = hits[0].message
    assert "geo" in msg and "newest:" in msg and "older:" in msg
    assert "descriptions identical" in msg
    assert ".claude" in msg and ".pi" in msg
    assert hits[0].severity == "warning"


def test_diverged_copies_skips_coloaded_same_name(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    # same harness loads both (project + user scope): name-shadow territory
    _write_named(proj, ".claude/skills", "tool", "project version")
    _write_named(home, ".claude/skills", "tool", "user version")
    findings = run_all(world_from(proj, home), Config())
    assert find(findings, "diverged-copies") == []
    assert find(findings, "name-shadow")


def test_diverged_copies_skips_identical_content(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    _write_named(proj, ".claude/skills", "same", "identical body")
    _write_named(proj, ".pi/skills", "same", "identical body")
    findings = run_all(world_from(proj, home, ("claude-code", "pi")), Config())
    assert find(findings, "diverged-copies") == []


def test_diverged_copies_tie_labels_and_diff_fix(tmp_path):
    import os
    proj, home = tmp_path / "p", tmp_path / "h"
    _write_named(proj, ".claude/skills", "geo", "body one")
    _write_named(proj, ".pi/skills", "geo", "body two")
    ts = 1750000000
    for rel in [".claude/skills/geo/SKILL.md", ".pi/skills/geo/SKILL.md"]:
        os.utime(proj / rel, (ts, ts))
    findings = run_all(world_from(proj, home, ("claude-code", "pi")), Config())
    hit = find(findings, "diverged-copies")[0]
    assert "newest:" not in hit.message and "copy:" in hit.message
    assert any(cmd.startswith("diff ") for cmd in hit.fix_commands)
