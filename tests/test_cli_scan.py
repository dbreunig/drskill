import json
from pathlib import Path

from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


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
