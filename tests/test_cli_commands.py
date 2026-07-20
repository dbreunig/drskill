import tomllib
from pathlib import Path

from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()

NEAR_DUP_BODY = (
    "Collect the metrics, summarize each stream, list risks with owners, "
    "and end with next steps for the team. " * 10
)


def env_for(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"DRSKILL_HOME": str(home)}


def write(proj, name, description, body):
    d = proj / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")


def invoke(tmp_path, *args):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    return runner.invoke(app, [*args, "--root", str(proj)], env=env_for(tmp_path))


def test_ack_silences_until_content_changes(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "a-writer", "Writes reports.", NEAR_DUP_BODY)
    write(proj, "b-writer", "Writes updates.", NEAR_DUP_BODY)
    # repeated-sentence bodies dedupe into few shingles; pin a threshold the
    # pair clears so the test exercises ack mechanics, not tuning
    (proj / "drskill.toml").write_text("[thresholds]\nnear_duplicate = 0.5\n")
    assert invoke(tmp_path, "scan", "--ci").exit_code == 2
    r = invoke(tmp_path, "ack", "near-duplicate", "a-writer", "b-writer", "--note", "intentional")
    assert r.exit_code == 0
    data = tomllib.loads((proj / "drskill.toml").read_text())
    assert data["ack"][0]["check"] == "near-duplicate"
    assert data["ack"][0]["note"] == "intentional"
    assert invoke(tmp_path, "scan", "--ci").exit_code == 0
    # content change resurfaces the finding
    f = proj / ".claude" / "skills" / "a-writer" / "SKILL.md"
    f.write_text(f.read_text() + "\nNew paragraph.\n")
    assert invoke(tmp_path, "scan", "--ci").exit_code == 2


def test_ack_no_match_errors(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "solo", "Fine.", "body")
    r = invoke(tmp_path, "ack", "near-duplicate", "solo", "ghost")
    assert r.exit_code == 1
    assert "No active finding" in r.output


def test_list_tokens(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "alpha", "First skill.", "body one")
    write(proj, "beta", "Second skill.", "body two")
    r = invoke(tmp_path, "list", "--tokens")
    assert r.exit_code == 0
    for expected in ["Claude Code", "alpha", "beta", "catalog", "body", "total"]:
        assert expected in r.output
    r2 = invoke(tmp_path, "list", "--harness", "pi")
    assert "alpha" not in r2.output


def test_init(tmp_path):
    r = invoke(tmp_path, "init")
    assert r.exit_code == 0
    proj = tmp_path / "proj"
    data = tomllib.loads((proj / "drskill.toml").read_text())
    assert data["budget"]["catalog_tokens_max"] == 6000
    assert data["thresholds"]["near_duplicate"] == 0.85
    assert "#" in (proj / "drskill.toml").read_text()  # keeps comments
    assert invoke(tmp_path, "init").exit_code == 1  # refuses overwrite
