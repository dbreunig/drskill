import json
from pathlib import Path

from drskill import mcp


def test_looks_secret_prefixes_names_and_references():
    assert mcp.looks_secret("ANY", "sk-ant-abcdef1234567890abcdef")
    assert mcp.looks_secret("ANY", "ghp_16charslong16charslong16charslong")
    assert mcp.looks_secret("GITHUB_TOKEN", "hunter2value")  # name rule
    assert mcp.looks_secret("API_KEY", "x" * 8)
    assert not mcp.looks_secret("GITHUB_TOKEN", "${GITHUB_TOKEN}")  # reference is fine
    assert not mcp.looks_secret("LOG_LEVEL", "debug")


def test_parse_mcp_json(tmp_path):
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps({"mcpServers": {
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                   "env": {"GITHUB_TOKEN": "ghp_16charslong16charslong16charslong"}},
        "remote": {"url": "http://example.com/sse"},
    }}))
    servers, errors = mcp.parse_config(p, "mcp-json", "claude-code", "project", tmp_path)
    assert errors == []
    by_name = {s.name: s for s in servers}
    gh = by_name["github"]
    assert gh.transport == "stdio" and gh.command == "npx"
    assert gh.env_names == ["GITHUB_TOKEN"] and gh.suspect_env == ["GITHUB_TOKEN"]
    assert "ghp_" not in gh.model_dump_json()  # the value is gone entirely
    rm = by_name["remote"]
    assert rm.transport == "http" and rm.url == "http://example.com/sse"


def test_parse_claude_user_json_scopes(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    p = tmp_path / ".claude.json"
    p.write_text(json.dumps({
        "mcpServers": {"global-srv": {"command": "uvx", "args": ["srv"]}},
        "projects": {str(proj.resolve()): {"mcpServers": {"proj-srv": {"command": "x"}}}},
    }))
    servers, errors = mcp.parse_config(p, "claude-user-json", "claude-code", "user", proj)
    scopes = {s.name: s.scope for s in servers}
    assert scopes == {"global-srv": "user", "proj-srv": "project"}


def test_parse_vscode_and_codex(tmp_path):
    v = tmp_path / "mcp.json"
    v.write_text(json.dumps({"servers": {"fetchy": {"command": "fetchy-bin"}}}))
    servers, _ = mcp.parse_config(v, "vscode-json", "copilot", "project", tmp_path)
    assert [s.name for s in servers] == ["fetchy"]
    c = tmp_path / "config.toml"
    c.write_text('[mcp_servers.docs]\ncommand = "docs-mcp"\nargs = ["--stdio"]\n')
    servers, _ = mcp.parse_config(c, "codex-toml", "codex", "user", tmp_path)
    assert servers[0].name == "docs" and servers[0].command == "docs-mcp"


def test_parse_error_is_reported_not_raised(tmp_path):
    p = tmp_path / ".mcp.json"
    p.write_text("{not json")
    servers, errors = mcp.parse_config(p, "mcp-json", "claude-code", "project", tmp_path)
    assert servers == [] and len(errors) == 1


def test_config_hash_ignores_values_and_orders(tmp_path):
    def entry(env):
        p = tmp_path / "a.json"
        p.write_text(json.dumps({"mcpServers": {"s": {"command": "c", "env": env}}}))
        (srv,), _ = mcp.parse_config(p, "mcp-json", "h", "project", tmp_path)
        return srv
    a = entry({"K1": "value-one", "K2": "x"})
    b = entry({"K2": "y", "K1": "value-two"})  # same names, different values/order
    assert a.config_hash == b.config_hash


from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.pipeline import run_scan


def test_harness_data_has_mcp_entries():
    hs = {h.id: h for h in load_harnesses()}
    assert ".mcp.json" in hs["claude-code"].mcp_project_configs
    assert "~/.claude.json" in hs["claude-code"].mcp_global_configs
    assert hs["claude-code"].mcp_format_global == "claude-user-json"
    assert "claude-desktop" in hs
    assert hs["claude-desktop"].project_paths == []  # no skills, only MCP


def test_run_scan_discovers_servers(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)  # detect claude-code
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
    }}))
    world, findings = run_scan(proj, home, config=Config())
    names = {(s.harness, s.name, s.scope) for s in world.mcp_servers}
    assert ("claude-code", "github", "project") in names


def test_run_scan_reports_config_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".mcp.json").write_text("{broken")
    world, findings = run_scan(proj, home, config=Config())
    assert world.mcp_config_errors and world.mcp_config_errors[0][0] == "claude-code"


def test_hash_and_public_material_is_not_a_secret():
    assert not mcp.looks_secret(
        "NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
        "a3f8c2d9e1b04756a3f8c2d9e1b04756a3f8c2d9e1b04756a3f8c2d9e1b04756",
    )
    assert not mcp.looks_secret("SERVER_FINGERPRINT", "AB:CD:EF:12:34:56:78:90:AB:CD:EF:12:34:56:78:90")
    assert not mcp.looks_secret("SSH_PUBLIC_KEY", "AAAAB3NzaC1yc2EAAAADAQABAAABgQ0000000000000000")
    # a known secret prefix still wins even under an innocent name
    assert mcp.looks_secret("CLIENT_SHA256S", "sk-ant-abcdef1234567890abcdef")
