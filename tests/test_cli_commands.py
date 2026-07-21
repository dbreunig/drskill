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
    write(proj, "a-writer", "Use when the user asks for a written report.", NEAR_DUP_BODY)
    write(proj, "b-writer", "Use when the user asks for a written update.", NEAR_DUP_BODY)
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


def test_ack_contributorless_lockfile_finding(tmp_path):
    import json

    proj = tmp_path / "proj"
    # distinct bodies so the pair doesn't also trip near-duplicate, which
    # would keep --ci non-zero for a reason unrelated to this ack
    write(proj, "a", "Use when the user works with alpha widgets.", "alpha body content here")
    write(proj, "b", "Use when the user works with beta widgets.", "totally different beta stuff")
    lock = {
        "skills": {
            "a": {"hash": "sha256-totally-wrong-a"},
            "b": {"hash": "sha256-totally-wrong-b"},
        }
    }
    (proj / "skills-lock.json").write_text(json.dumps(lock))
    assert invoke(tmp_path, "scan", "--ci").exit_code == 2
    r = invoke(tmp_path, "ack", "lockfile-drift")
    assert r.exit_code == 0
    assert invoke(tmp_path, "scan", "--ci").exit_code == 0


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


def test_list_survives_markup_in_skill_names(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "sneaky", "d", "body")
    f = proj / ".claude" / "skills" / "sneaky" / "SKILL.md"
    f.write_text("---\nname: '[/bold]sneaky'\ndescription: d\n---\nbody\n")
    r = invoke(tmp_path, "list", "--tokens")
    assert r.exit_code == 0
    assert r.exception is None


def test_list_unknown_harness_errors(tmp_path):
    r = invoke(tmp_path, "list", "--harness", "bogus")
    assert r.exit_code == 1
    assert "unknown harness" in r.output and "claude-code" in r.output


def test_list_all_shows_empty_harnesses(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "alpha", "First.", "body")
    (proj / ".pi").mkdir()
    r = invoke(tmp_path, "list")
    assert "Pi" not in r.output
    r_all = invoke(tmp_path, "list", "--all")
    assert "Pi" in r_all.output


def test_list_scoped_undetected_harness_shows_empty_table(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "alpha", "First.", "body")
    r = invoke(tmp_path, "list", "--harness", "qwen-code")
    assert r.exit_code == 0
    assert "Qwen Code" in r.output
    assert "not detected" in r.output


# activation-less AND mutually non-overlapping, so only missing-activation fires
NOISY = {
    "one": "Formats source code files.",
    "two": "Renders vector diagrams cleanly.",
    "three": "Optimizes database index layouts.",
}


def _mk(proj, name):
    write(proj, name, NOISY[name], f"body of {name}")


def _short_ids(output):
    import re
    return re.findall(r"^  \[([0-9a-f]{4})\]", output, re.M)


def test_ack_by_short_id(tmp_path):
    proj = tmp_path / "proj"
    _mk(proj, "one")
    r = invoke(tmp_path, "scan")
    sid = _short_ids(r.output)[0]
    r2 = invoke(tmp_path, "ack", sid, "--note", "seen")
    assert r2.exit_code == 0 and "missing-activation" in r2.output
    assert invoke(tmp_path, "scan", "--ci").exit_code == 0


def test_ack_several_short_ids(tmp_path):
    proj = tmp_path / "proj"
    _mk(proj, "one")
    # a second finding of a different check, since same-check advisory
    # findings now merge into one cluster
    write(proj, "vague", "Helps with various tasks.", "b")
    r = invoke(tmp_path, "scan")
    sids = sorted(set(_short_ids(r.output)))
    assert len(sids) == 2
    r2 = invoke(tmp_path, "ack", *sids)
    assert r2.exit_code == 0
    assert invoke(tmp_path, "scan", "--ci").exit_code == 0


def test_ack_check_all(tmp_path):
    proj = tmp_path / "proj"
    for n in ["one", "two", "three"]:
        _mk(proj, n)
    r = invoke(tmp_path, "ack", "missing-activation", "--all")
    assert r.exit_code == 0
    # the three offenders merge into one cluster finding naming them all
    assert "missing-activation" in r.output
    for name in ("one", "two", "three"):
        assert name in r.output
    assert invoke(tmp_path, "scan", "--ci").exit_code == 0


def test_ack_all_everything(tmp_path):
    proj = tmp_path / "proj"
    _mk(proj, "one")
    write(proj, "vague", "Helps with various tasks.", "b")
    r = invoke(tmp_path, "ack", "--all", "--note", "baseline")
    assert r.exit_code == 0
    assert invoke(tmp_path, "scan", "--ci").exit_code == 0
    import tomllib
    data = tomllib.loads((proj / "drskill.toml").read_text())
    assert all(a.get("note") == "baseline" for a in data["ack"])
    # merged clusters: one missing-activation + one generic-description (+ overlap)
    assert len(data["ack"]) >= 2


def test_ack_unknown_id_errors(tmp_path):
    proj = tmp_path / "proj"
    _mk(proj, "one")
    r = invoke(tmp_path, "ack", "beef")
    assert r.exit_code == 1
    assert "No active finding" in r.output


def test_bare_check_id_acks_whole_class(tmp_path):
    proj = tmp_path / "proj"
    for n in ["one", "two", "three"]:
        _mk(proj, n)
    r = invoke(tmp_path, "ack", "missing-activation")
    assert r.exit_code == 0
    assert "missing-activation" in r.output
    for name in ("one", "two", "three"):
        assert name in r.output
    assert invoke(tmp_path, "scan", "--ci").exit_code == 0


def _mk_global(tmp_path, name, description):
    home = tmp_path / "home"
    d = home / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n"
    )


def test_machine_level_finding_acks_globally(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (tmp_path / "home").mkdir(exist_ok=True)
    _mk_global(tmp_path, "gskill", "Formats code.")  # missing-activation, user scope
    r = invoke(tmp_path, "ack", "missing-activation")
    assert r.exit_code == 0
    assert "machine-level" in r.output
    home_ledger = tmp_path / "home" / ".drskill.toml"
    assert home_ledger.exists() and "missing-activation" in home_ledger.read_text()
    assert not (proj / "drskill.toml").exists()
    # the merged config silences the finding on the next project scan
    assert invoke(tmp_path, "scan", "--ci").exit_code == 0


def test_local_flag_forces_project_ledger(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (tmp_path / "home").mkdir(exist_ok=True)
    _mk_global(tmp_path, "gskill", "Formats code.")
    r = invoke(tmp_path, "ack", "missing-activation", "--local")
    assert r.exit_code == 0
    assert (proj / "drskill.toml").exists()
    assert not (tmp_path / "home" / ".drskill.toml").exists()


def test_show_by_id_and_check(tmp_path):
    proj = tmp_path / "proj"
    _mk(proj, "one")
    write(proj, "vague", "Helps with various tasks.", "b")
    r = invoke(tmp_path, "scan")
    sids = sorted(set(_short_ids(r.output)))
    assert len(sids) == 2
    r_id = invoke(tmp_path, "show", sids[0])
    assert r_id.exit_code == 0
    assert sids[0] in r_id.output
    r_check = invoke(tmp_path, "show", "missing-activation")
    assert r_check.exit_code == 0
    assert "missing-activation" in r_check.output and "one" in r_check.output
    r_bad = invoke(tmp_path, "show", "dead")
    assert r_bad.exit_code == 1
    assert "No active finding" in r_bad.output


def test_show_does_not_write_state(tmp_path):
    proj = tmp_path / "proj"
    _mk(proj, "one")
    r = invoke(tmp_path, "show", "missing-activation")
    assert r.exit_code == 0
    assert not (tmp_path / "home" / ".drskill").exists()
