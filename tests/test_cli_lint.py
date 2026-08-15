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


def test_bad_max_calls_exits_two(tmp_path):
    # Regression: lint's --max-calls validation was copied from scan, which
    # exits 1 for usage errors. lint's contract reserves exit 2 for usage
    # errors (see test_usage_error_exits_two above), so a bad --max-calls
    # value must exit 2, not 1. The bad-value path returns before any model
    # setup (deep_llm import / build_judge), so this needs no API key.
    make_plugin(tmp_path / "p")
    r = runner.invoke(
        app, ["lint", str(tmp_path / "p"), "--deep", "--max-calls", "bogus"]
    )
    assert r.exit_code == 2, r.output


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


def test_lint_claude_plugin_end_to_end(tmp_path):
    # a claude-code plugin with a bad-name manifest and one good skill
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "Bad_Name"}')
    d = root / "skills" / "good"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: good\ndescription: Use when the user asks for a "
        "good-skill demo.\n---\nbody\n"
    )
    r = runner.invoke(app, ["lint", str(root)])
    assert r.exit_code == 1  # error-severity finding present
    assert "cc-manifest-invalid" in r.output


def test_lint_marketplace_end_to_end(tmp_path):
    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "m", "owner": {"name": "o"},
        "plugins": [{"name": "x", "source": {"source": "github", "repo": "o/r"}}],
    }))
    r = runner.invoke(app, ["lint", str(root)])
    # warning severity only -> default --fail-on error passes
    assert r.exit_code == 0
    assert "marketplace-unpinned-source" in r.output


def test_lint_dual_manifest_runs_both_suites(tmp_path):
    from drskill.lint import checks_for, classify

    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "p"}')
    (root / "plugin.json").write_text('{"name": "p"}')
    ids = checks_for(classify(root), mcp_connect=False)
    assert "plugin-manifest-invalid" in ids and "cc-manifest-invalid" in ids
    assert "marketplace-invalid" in ids


def test_marketplace_target_gets_only_marketplace_checks(tmp_path):
    from drskill.lint import MARKETPLACE_CHECKS, checks_for, classify

    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text("{}")
    assert checks_for(classify(root), mcp_connect=False) == MARKETPLACE_CHECKS


def make_marketplace(root: Path, entries=None):
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    data = {
        "name": "test-market", "owner": {"name": "o"},
        "plugins": entries if entries is not None else [
            {"name": "loose-plugin",
             "source": {"source": "github", "repo": "o/r"}},
        ],
    }
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps(data))


def test_ack_lint_by_check_id_silences_relint(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    root = tmp_path / "market"
    make_marketplace(root)
    r = runner.invoke(app, ["lint", str(root), "--fail-on", "warn"])
    assert r.exit_code == 1 and "marketplace-unpinned-source" in r.output
    r2 = runner.invoke(app, ["ack", "--lint", str(root), "marketplace-unpinned-source"])
    assert r2.exit_code == 0, r2.output
    ledger = root / "drskill.toml"
    assert ledger.is_file() and "marketplace-unpinned-source" in ledger.read_text()
    r3 = runner.invoke(app, ["lint", str(root), "--fail-on", "warn"])
    assert r3.exit_code == 0, r3.output


def test_ack_lint_by_finding_id(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    root = tmp_path / "market"
    make_marketplace(root)
    r = runner.invoke(app, ["lint", str(root), "--json"])
    findings = json.loads(r.output)
    fp = next(f["fingerprint"] for f in findings
              if f["check_id"] == "marketplace-unpinned-source")
    short_id = fp.split(":", 1)[1][:4]
    r2 = runner.invoke(app, ["ack", "--lint", str(root), short_id])
    assert r2.exit_code == 0, r2.output
    assert fp in (root / "drskill.toml").read_text()


def test_ack_lint_all(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    root = tmp_path / "market"
    make_marketplace(root, entries=[
        {"name": "loose-a", "source": {"source": "github", "repo": "o/a"}},
        {"name": "loose-b", "source": {"source": "npm", "package": "b"}},
    ])
    r = runner.invoke(app, ["ack", "--lint", str(root), "--all"])
    assert r.exit_code == 0, r.output
    text = (root / "drskill.toml").read_text()
    assert text.count("[[ack]]") == 2


def test_ack_lint_rejects_scope_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    root = tmp_path / "market"
    make_marketplace(root)
    for extra in (["--local"], ["--global-ack"], ["--global"]):
        r = runner.invoke(app, ["ack", "--lint", str(root),
                                "marketplace-unpinned-source", *extra])
        assert r.exit_code == 1, (extra, r.output)


def test_ack_lint_bad_target_exits_one(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    empty = tmp_path / "empty"
    empty.mkdir()
    r = runner.invoke(app, ["ack", "--lint", str(empty), "marketplace-invalid"])
    assert r.exit_code == 1, r.output
