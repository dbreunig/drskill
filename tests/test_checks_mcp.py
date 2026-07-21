import json
import shutil
from pathlib import Path

from drskill.ledger import Config
from drskill.pipeline import run_scan


def project_with(tmp_path, mcp: dict, home_claude_json: dict | None = None):
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": mcp}))
    if home_claude_json is not None:
        home.mkdir(exist_ok=True)
        (home / ".claude.json").write_text(json.dumps(home_claude_json))
    return proj, home


def by_check(findings, check_id):
    return [f for f in findings if f.check_id == check_id]


def scan(proj, home, monkeypatch, tmp_path):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    return run_scan(proj, home, config=Config())


def test_secret_in_project_config_is_error(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {
        "gh": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": "ghp_16charslong16charslong16charslong"}},
    })
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    (f,) = by_check(findings, "mcp-secret-in-config")
    assert f.severity == "error"
    assert "GITHUB_TOKEN" in f.message
    assert "ghp_" not in f.message  # never the value
    assert "ghp_" not in f.fingerprint


def test_secret_in_user_config_is_warning(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {}, home_claude_json={
        "mcpServers": {"gh": {"command": "gh-mcp", "env": {"API_KEY": "literal-value"}}},
    })
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    (f,) = by_check(findings, "mcp-secret-in-config")
    assert f.severity == "warning"


def test_unpinned_and_insecure_and_dead(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {
        "floaty": {"command": "npx", "args": ["-y", "@scope/server-pkg"]},
        "plain": {"url": "http://mcp.example.com/sse"},
        "local": {"url": "http://localhost:3000/sse"},
        "ghost": {"command": "definitely-not-a-real-binary-xyz"},
        "alive": {"command": shutil.which("ls") or "/bin/ls"},
    })
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    assert {f.contributor_names[0] for f in by_check(findings, "mcp-unpinned-server")} == {"floaty"}
    assert {f.contributor_names[0] for f in by_check(findings, "mcp-insecure-url")} == {"plain"}
    dead = by_check(findings, "mcp-dead-server")
    assert {f.contributor_names[0] for f in dead} == {"ghost"}
    assert dead[0].severity == "error"


def test_pinned_version_is_clean(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {
        "pinned": {"command": "npx", "args": ["-y", "@scope/server-pkg@1.2.3"]},
    })
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    assert by_check(findings, "mcp-unpinned-server") == []


def test_shadowed_server_same_harness(tmp_path, monkeypatch):
    proj, home = project_with(
        tmp_path,
        {"gh": {"command": "gh-mcp-project"}},
        home_claude_json={"mcpServers": {"gh": {"command": "gh-mcp-user"}}},
    )
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    (f,) = by_check(findings, "mcp-shadowed-server")
    assert "gh" in f.contributor_names
    assert "project" in f.message  # claude-code is project-first


def test_diverged_across_harnesses(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {"gh": {"command": "gh-mcp", "args": ["--fast"]}})
    (proj / ".cursor").mkdir()
    (proj / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {
        "gh": {"command": "gh-mcp", "args": ["--slow"]},
    }}))
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    (f,) = by_check(findings, "mcp-diverged-server")
    assert "args" in f.message


def test_identical_across_harnesses_is_clean(tmp_path, monkeypatch):
    proj, home = project_with(tmp_path, {"gh": {"command": "gh-mcp"}})
    (proj / ".cursor").mkdir()
    (proj / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {
        "gh": {"command": "gh-mcp"},
    }}))
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    assert by_check(findings, "mcp-diverged-server") == []
    assert by_check(findings, "mcp-shadowed-server") == []


def test_invalid_config_finding(tmp_path, monkeypatch):
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".mcp.json").write_text("{broken")
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    (f,) = by_check(findings, "mcp-config-invalid")
    assert f.severity == "error"


from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


def env_for(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"DRSKILL_HOME": str(home), "COLUMNS": "200"}


def test_header_counts_servers(tmp_path):
    proj, _ = project_with(tmp_path, {"gh": {"command": "gh-mcp-not-installed-xyz"}})
    r = runner.invoke(app, ["scan", "--root", str(proj)], env=env_for(tmp_path))
    assert "1 MCP server" in r.output
    assert "mcp-dead-server" in r.output


def test_list_mcp_table(tmp_path):
    proj, _ = project_with(tmp_path, {"gh": {"command": "gh-mcp"}})
    r = runner.invoke(app, ["list", "--mcp", "--root", str(proj)], env=env_for(tmp_path))
    assert r.exit_code == 0, r.output
    assert "gh" in r.output and "stdio" in r.output and "project" in r.output


def test_user_scope_mcp_ack_routes_to_machine_ledger(tmp_path):
    proj, home = project_with(tmp_path, {}, home_claude_json={
        "mcpServers": {"gh": {"command": "gh-mcp", "env": {"API_KEY": "literal-value"}}},
    })
    r = runner.invoke(
        app, ["ack", "mcp-secret-in-config", "--root", str(proj)], env=env_for(tmp_path)
    )
    assert r.exit_code == 0, r.output
    assert (home / ".drskill.toml").is_file()
    assert not (proj / "drskill.toml").exists()


def test_relative_path_command_is_not_declared_dead(tmp_path, monkeypatch):
    """A relative command resolves against a cwd we cannot know statically."""
    proj, home = project_with(tmp_path, {
        "bundle": {"command": "./Some App.app/Contents/MacOS/helper"},
    })
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    assert by_check(findings, "mcp-dead-server") == []


def test_unpinned_detected_through_absolute_runner_path(tmp_path, monkeypatch):
    """Real machine: Desktop configs run npx via an asdf shim's full path."""
    proj, home = project_with(tmp_path, {
        "memory": {"command": "/Users/x/.asdf/shims/npx",
                   "args": ["-y", "@modelcontextprotocol/server-memory"]},
        "playwright": {"command": "/usr/local/bin/npx",
                       "args": ["@playwright/mcp@latest", "--headless"]},
    })
    _, findings = scan(proj, home, monkeypatch, tmp_path)
    hit = {f.contributor_names[0] for f in by_check(findings, "mcp-unpinned-server")}
    assert hit == {"memory", "playwright"}
