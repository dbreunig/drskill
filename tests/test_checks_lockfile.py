import json
import shlex
from pathlib import Path

from drskill.checks import run_all
from drskill.checks.lockfile import compute_tree_hash, load_lockfile
from drskill.discovery import discover
from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.pipeline import run_scan

PAYLOAD = "'; echo pwned; '"


def write(root, name, body="body"):
    d = root / ".claude" / "skills" / name
    d.mkdir(parents=True)
    # double-quoted YAML scalar so adversarial names (e.g. embedded single
    # quotes) survive frontmatter parsing unmangled
    (d / "SKILL.md").write_text(f'---\nname: "{name}"\ndescription: d\n---\n{body}\n')
    return d


def test_load_lockfile_tolerant(tmp_path):
    assert load_lockfile(tmp_path) is None
    (tmp_path / "skills-lock.json").write_text("not json {")
    assert load_lockfile(tmp_path) is None
    (tmp_path / "skills-lock.json").write_text(json.dumps({"skills": {"a": {"hash": "x"}}}))
    assert load_lockfile(tmp_path) == {"a": {"hash": "x"}}
    (tmp_path / "skills-lock.json").write_text(json.dumps({"b": {"hash": "y"}}))
    assert load_lockfile(tmp_path) == {"b": {"hash": "y"}}


def test_tree_hash_changes_with_content(tmp_path):
    d = write(tmp_path, "s")
    h1 = compute_tree_hash(d)
    (d / "SKILL.md").write_text("changed")
    assert compute_tree_hash(d) != h1


def test_drift_and_missing(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    home.mkdir()
    d_ok = write(proj, "pinned")
    d_drift = write(proj, "drifted")
    lock = {
        "skills": {
            "pinned": {"hash": compute_tree_hash(d_ok)},
            "drifted": {"hash": "sha256-not-what-is-on-disk"},
            "ghost": {"hash": "abc"},
        }
    }
    (proj / "skills-lock.json").write_text(json.dumps(lock))
    _world, findings = run_scan(proj, home)
    drift = [f for f in findings if f.check_id == "lockfile-drift"]
    messages = " | ".join(f.message for f in drift)
    assert "drifted" in messages and "modified outside" in messages
    assert "ghost" in messages and "not found" in messages
    assert "pinned" not in messages


def test_missing_fix_command_quotes_adversarial_source(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    proj.mkdir()
    home.mkdir()
    lock = {"skills": {"ghost": {"hash": "abc", "source": PAYLOAD}}}
    (proj / "skills-lock.json").write_text(json.dumps(lock))
    _world, findings = run_scan(proj, home)
    drift = [f for f in findings if f.check_id == "lockfile-drift"]
    assert len(drift) == 1
    cmd = drift[0].fix_commands[0]
    assert shlex.split(cmd)[-1] == PAYLOAD


def test_drift_fix_command_quotes_adversarial_skill_name(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    home.mkdir()
    d_ok = write(proj, "pinned")
    d_drift = write(proj, PAYLOAD)
    lock = {
        "skills": {
            "pinned": {"hash": compute_tree_hash(d_ok)},
            PAYLOAD: {"hash": "sha256-not-what-is-on-disk"},
        }
    }
    (proj / "skills-lock.json").write_text(json.dumps(lock))
    _world, findings = run_scan(proj, home)
    drift = [f for f in findings if f.check_id == "lockfile-drift" and PAYLOAD in f.message]
    assert len(drift) == 1
    update_cmd = next(c for c in drift[0].fix_commands if c.startswith("npx skills update"))
    assert shlex.split(update_cmd.split("#")[0])[-1] == PAYLOAD


def test_all_mismatch_collapses_to_single_unverifiable_warning(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    home.mkdir()
    write(proj, "a")
    write(proj, "b")
    lock = {
        "skills": {
            "a": {"hash": "sha256-totally-wrong-a"},
            "b": {"hash": "sha256-totally-wrong-b"},
        }
    }
    (proj / "skills-lock.json").write_text(json.dumps(lock))
    _world, findings = run_scan(proj, home)
    drift = [f for f in findings if f.check_id == "lockfile-drift"]
    assert len(drift) == 1
    assert "cannot reproduce" in drift[0].message
