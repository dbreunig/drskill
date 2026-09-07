import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


def write(proj: Path, name: str, description: str, body: str):
    d = proj / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    # wide console so string assertions never straddle a wrapped line
    monkeypatch.setenv("COLUMNS", "200")
    proj = tmp_path / "proj"
    proj.mkdir()
    write(proj, "pdf", "Use when the user asks to summarize pdf documents.", "a" * 40)
    write(proj, "deploy", "Use when the user asks to deploy kubernetes clusters.", "b" * 40)
    monkeypatch.chdir(proj)
    return proj


def test_explain_prints_ranking_and_disclaimer(project):
    result = runner.invoke(app, ["explain", "summarize a pdf document"])
    assert result.exit_code == 0, result.output
    assert "routes to pdf" in result.output
    assert "pdf" in result.output
    assert "drskill's own similarity model" in result.output


def test_explain_json_shape(project):
    result = runner.invoke(app, ["explain", "summarize a pdf document", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["query"] == "summarize a pdf document"
    assert data["harnesses"][0]["rows"][0]["name"] == "pdf"


def test_explain_unknown_harness_errors(project):
    result = runner.invoke(app, ["explain", "x", "--harness", "not-real"])
    assert result.exit_code == 1


def test_explain_is_read_only(project):
    runner.invoke(app, ["explain", "summarize a pdf document"])
    assert not (project / ".drskill").exists()
