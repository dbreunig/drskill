import json
from pathlib import Path

from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()

GOOD_MANIFEST = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": "demo-plugin",
}


def make_plugin(root: Path, manifest=None, skill_ok=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps(manifest or GOOD_MANIFEST))
    d = root / "skills" / "alpha"
    d.mkdir(parents=True)
    name = "alpha" if skill_ok else "wrong-name"
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when the user asks to test alpha.\n---\nbody\n"
    )


def test_clean_plugin_exits_zero(tmp_path):
    make_plugin(tmp_path / "p")
    r = runner.invoke(app, ["lint", str(tmp_path / "p")])
    assert r.exit_code == 0, r.output
    assert "No findings" in r.output


def test_error_exits_one(tmp_path):
    make_plugin(tmp_path / "p", manifest={"name": "demo-plugin"})  # no $schema
    r = runner.invoke(app, ["lint", str(tmp_path / "p")])
    assert r.exit_code == 1
    assert "plugin-manifest-invalid" in r.output


def test_warning_passes_by_default_fails_with_fail_on_warn(tmp_path):
    make_plugin(tmp_path / "p", manifest={**GOOD_MANIFEST, "surprise": 1})
    r = runner.invoke(app, ["lint", str(tmp_path / "p")])
    assert r.exit_code == 0
    r = runner.invoke(app, ["lint", str(tmp_path / "p"), "--fail-on", "warn"])
    assert r.exit_code == 1


def test_usage_error_exits_two(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = runner.invoke(app, ["lint", str(empty)])
    assert r.exit_code == 2
    r = runner.invoke(app, ["lint", str(empty), "--fail-on", "bogus"])
    assert r.exit_code == 2


def test_json_output(tmp_path):
    make_plugin(tmp_path / "p", manifest={"name": "demo-plugin"})
    r = runner.invoke(app, ["lint", str(tmp_path / "p"), "--json"])
    assert r.exit_code == 1
    payload = json.loads(r.output)
    assert any(f["check_id"] == "plugin-manifest-invalid" for f in payload)


def test_skill_file_target(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: other\ndescription: d\n---\nb\n")
    r = runner.invoke(app, ["lint", str(d / "SKILL.md")])
    assert r.exit_code == 1
    assert "spec-name-mismatch" in r.output


def test_ack_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "p"
    make_plugin(root, manifest={**GOOD_MANIFEST, "surprise": 1})
    (root / "drskill.toml").write_text("")
    r = runner.invoke(app, ["lint", str(root), "--fail-on", "warn", "--json"])
    payload = json.loads(r.output)[0]
    from drskill import ledger

    # ledger.Ack requires check/skills/fingerprint (src/drskill/ledger.py:35-40);
    # construct it the way cli.py's `ack` command does, from the finding's own
    # fields, rather than the brief's fingerprint-only call.
    ledger.append_ack(
        root / "drskill.toml",
        ledger.Ack(
            check=payload["check_id"],
            skills=sorted(payload["contributor_names"]),
            fingerprint=payload["fingerprint"],
        ),
    )
    r = runner.invoke(app, ["lint", str(root), "--fail-on", "warn"])
    assert r.exit_code == 0, r.output
