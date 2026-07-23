import tomllib

from typer.testing import CliRunner

import drskill.cli as cli
from drskill.cli import app

runner = CliRunner()


def env_for(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"DRSKILL_HOME": str(home)}


def write_project_skill(proj, name, description):
    d = proj / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n"
    )


def write_global_skill(tmp_path, name, description):
    d = tmp_path / "home" / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n"
    )


def invoke(tmp_path, *args):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    return runner.invoke(app, [*args, "--root", str(proj)], env=env_for(tmp_path))


def keys(*seq):
    it = iter(seq)
    return lambda: next(it)


def allow_interactive(monkeypatch):
    monkeypatch.setattr(cli.interactive, "can_interact", lambda *a, **k: None)


def two_finding_setup(tmp_path):
    """One machine-level finding (global generic description) and one
    project finding (project generic description). Distinct checks are not
    needed; distinct scopes are."""
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    write_global_skill(tmp_path, "gskill", "Formats code.")  # missing-activation
    write_project_skill(proj, "vague", "Helps with various tasks.")  # generic + missing
    return proj


def test_review_refuses_without_tty(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    write_project_skill(proj, "vague", "Helps with various tasks.")
    r = invoke(tmp_path, "review")
    assert r.exit_code == 1
    assert "review is interactive" in r.output


def test_review_ack_routes_and_persists(tmp_path, monkeypatch):
    proj = two_finding_setup(tmp_path)
    allow_interactive(monkeypatch)
    # findings: generic-description (vague, project), missing-activation
    # (gskill + vague -> mixed). Ack the first, quit.
    monkeypatch.setattr(cli, "key_source", keys("a", "q"))
    r = invoke(tmp_path, "review")
    assert r.exit_code == 0
    assert "acked" in r.output
    assert (proj / "drskill.toml").exists()
    r2 = invoke(tmp_path, "scan", "--json")
    assert r2.exit_code == 0


def test_review_global_only_ack_goes_to_machine_ledger(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    write_global_skill(tmp_path, "gskill", "Formats code.")  # the only finding
    allow_interactive(monkeypatch)
    monkeypatch.setattr(cli, "key_source", keys("a"))
    r = invoke(tmp_path, "review")
    assert r.exit_code == 0
    home_ledger = tmp_path / "home" / ".drskill.toml"
    assert home_ledger.exists()
    assert "missing-activation" in home_ledger.read_text()
    assert not (proj / "drskill.toml").exists()
    assert "~/.drskill.toml" in r.output


def test_review_note_and_fix_queue(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    write_project_skill(proj, "vague", "Helps with various tasks.")
    allow_interactive(monkeypatch)
    # two findings: generic-description and missing-activation (order by check)
    monkeypatch.setattr(cli, "key_source", keys("n", "f"))
    monkeypatch.setattr(cli, "line_source", lambda prompt: "my note")
    monkeypatch.setattr(cli, "_to_clipboard", lambda text: False)  # keep the real clipboard
    r = invoke(tmp_path, "review")
    assert r.exit_code == 0
    data = tomllib.loads((proj / "drskill.toml").read_text())
    assert data["ack"][0]["note"] == "my note"
    assert "queued fix commands" in r.output
    assert "undecided" not in r.output


def test_review_quit_preserves_progress(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    write_project_skill(proj, "vague", "Helps with various tasks.")
    allow_interactive(monkeypatch)
    monkeypatch.setattr(cli, "key_source", keys("a", "q"))
    r = invoke(tmp_path, "review")
    assert r.exit_code == 0
    assert "1 finding left undecided" in r.output
    data = tomllib.loads((proj / "drskill.toml").read_text())
    assert len(data["ack"]) == 1


def test_review_quit_leaves_undisplayed_findings_new(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    # two findings: generic-description and missing-activation on 'vague'
    write_project_skill(proj, "vague", "Helps with various tasks.")
    allow_interactive(monkeypatch)
    monkeypatch.setattr(cli, "key_source", keys("q"))  # quit at finding 1
    r = invoke(tmp_path, "review")
    assert r.exit_code == 0
    r2 = invoke(tmp_path, "scan")
    # the displayed finding is seen; the never-displayed one is still new
    assert "1 new" in r2.output


def test_review_routes_to_both_ledgers_in_one_session(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    # machine-level finding: global skill missing activation
    write_global_skill(tmp_path, "gskill", "Formats code.")
    # project finding with activation phrasing but no distinctive words,
    # so only generic-description fires and stays project-scoped
    write_project_skill(proj, "vague", "Use when needed.")
    allow_interactive(monkeypatch)
    monkeypatch.setattr(cli, "key_source", keys("a", "a"))
    r = invoke(tmp_path, "review")
    assert r.exit_code == 0
    proj_ledger = (proj / "drskill.toml").read_text()
    home_ledger = (tmp_path / "home" / ".drskill.toml").read_text()
    assert "generic-description" in proj_ledger
    assert "missing-activation" in home_ledger
    assert "drskill.toml" in r.output and "~/.drskill.toml" in r.output


def test_review_ack_writes_approved_baseline(tmp_path, monkeypatch):
    import json

    from drskill.harnesses import load_harnesses
    from drskill.mcp import discover_servers
    from drskill.mcp_connect import (
        ServerSnapshot, ToolInfo, approved_dir, load_snapshots, save_snapshot,
        snapshot_dir,
    )
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)
    home.mkdir(exist_ok=True)
    # "true" is a real binary on PATH, so this doesn't also trip
    # mcp-dead-server and add an unrelated finding to ack.
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"srv": {"command": "true"}}}))
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    cfg = servers[0].config_hash
    save_snapshot(snapshot_dir(proj, home, False), ServerSnapshot(
        server="srv", config_hash=cfg, date="2026-07-22",
        tools=[ToolInfo(name="run", description="Runs a query. But ALSO CHANGED.",
                        schema_tokens=2)]))
    # a prior ack that no longer matches makes the finding a WARNING, so
    # review shows it (review skips notes)
    (proj / "drskill.toml").write_text(
        '[[ack]]\ncheck = "mcp-tools-unreviewed"\nskills = ["srv"]\n'
        'fingerprint = "sha256:stale"\n'
    )
    allow_interactive(monkeypatch)
    monkeypatch.setattr(cli, "key_source", keys("a"))
    r = invoke(tmp_path, "review")
    assert r.exit_code == 0
    approved = load_snapshots(approved_dir(snapshot_dir(proj, home, False)))
    assert cfg in approved
