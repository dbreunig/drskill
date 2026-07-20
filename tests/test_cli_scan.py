import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


def running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def env_for(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"DRSKILL_HOME": str(home)}


def write(proj: Path, name: str, content: str):
    d = proj / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(content)


def scan(tmp_path, *args):
    proj = tmp_path / "proj"
    return runner.invoke(
        app, ["scan", "--root", str(proj), *args], env=env_for(tmp_path)
    )


def test_clean_exits_zero(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "clean", "---\nname: clean\ndescription: fine\n---\nb\n")
    r = scan(tmp_path)
    assert r.exit_code == 0
    assert "No findings" in r.output


def test_error_exits_one(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "bad", "---\nname: mismatch\ndescription: d\n---\nb\n")
    r = scan(tmp_path)
    assert r.exit_code == 1
    assert "spec-name-mismatch" in r.output


def test_warning_exits_zero_without_ci_two_with(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "angle", "---\nname: angle\ndescription: use <x>\n---\nb\n")
    assert scan(tmp_path).exit_code == 0
    assert scan(tmp_path, "--ci").exit_code == 2


def test_json_output(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "bad", "---\nname: mismatch\ndescription: d\n---\nb\n")
    r = scan(tmp_path, "--json")
    data = json.loads(r.output)
    assert data[0]["check_id"] == "spec-name-mismatch"
    assert r.exit_code == 1


def test_global_mode_ignores_project(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "bad", "---\nname: mismatch\ndescription: d\n---\nb\n")
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    r = scan(tmp_path, "--global")
    assert r.exit_code == 0


def test_bare_invocation_shows_usage_not_traceback(tmp_path):
    r = runner.invoke(app, [], env=env_for(tmp_path))
    assert r.exit_code == 2
    assert "Traceback" not in r.output


def test_malformed_config_reports_clean_error_not_traceback(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "drskill.toml").write_text('budget = "oops"\n')
    r = scan(tmp_path)
    assert r.exit_code == 1
    assert "error:" in r.output
    assert "Traceback" not in r.output


def test_unreadable_skill_does_not_crash_scan_and_is_reported(tmp_path):
    if running_as_root():
        pytest.skip("root ignores file permissions")
    proj = tmp_path / "proj"
    write(proj, "locked", "---\nname: locked\ndescription: d\n---\nb\n")
    f = proj / ".claude" / "skills" / "locked" / "SKILL.md"
    f.chmod(0)
    try:
        r = scan(tmp_path)
        assert r.exception is None
        assert "Traceback" not in r.output
        assert "unreadable-skill" in r.output
    finally:
        f.chmod(0o644)


def test_scan_detailed_appends_tables(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "clean", "---\nname: clean\ndescription: fine\n---\nb\n")
    r = scan(tmp_path, "--detailed")
    assert r.exit_code == 0
    assert "No findings" in r.output and "clean" in r.output and "Claude Code" in r.output


def test_scan_json_wins_over_detailed(tmp_path):
    import json
    proj = tmp_path / "proj"
    write(proj, "clean", "---\nname: clean\ndescription: fine\n---\nb\n")
    r = scan(tmp_path, "--detailed", "--json")
    json.loads(r.output)


def test_scan_unknown_harness_errors(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    r = scan(tmp_path, "--harness", "bogus")
    assert r.exit_code == 1 and "unknown harness" in r.output


def test_scan_scoped_harness_drops_cross_harness_findings(tmp_path):
    proj = tmp_path / "proj"
    content = "---\nname: same\ndescription: d\n---\nbody\n"
    for rel in [".claude/skills/same", ".pi/skills/same"]:
        d = proj / rel
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(content)
    full = scan(tmp_path)
    assert "exact-duplicate" in full.output
    scoped = scan(tmp_path, "--harness", "pi")
    assert "exact-duplicate" not in scoped.output
    assert scoped.exit_code == 0


def test_scan_valid_but_undetected_harness_notes_and_passes(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "clean", "---\nname: clean\ndescription: fine\n---\nb\n")
    r = scan(tmp_path, "--harness", "qwen-code")
    assert r.exit_code == 0
    assert "not detected" in r.output
