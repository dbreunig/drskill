import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from typer.testing import CliRunner

from drskill import content, service
from drskill.cli import app

runner = CliRunner()

FILES = [
    {"path": "SKILL.md", "data": b"# Vector\n", "executable": False},
    {"path": "scripts/run.sh", "data": b"#!/bin/sh\necho hi\n", "executable": True},
]
HASH = content.manifest_hash(FILES)

GH_SKILL_MD = b"---\nname: citation\ndescription: d\n---\nbody\n"
GH_FILES = [
    {"path": "SKILL.md", "data": GH_SKILL_MD, "executable": False},
    {"path": "reference/tips.md", "data": b"tips\n", "executable": False},
]
GH_HASH = content.manifest_hash(GH_FILES)


def repo_tarball():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in GH_FILES:
            info = tarfile.TarInfo(f"pack-abc/skills/citation/{f['path']}")
            info.size = len(f["data"])
            tar.addfile(info, io.BytesIO(f["data"]))
    return buf.getvalue()


def hosted_entry():
    return {"kind": "skill", "selector": "skill:vector", "name": "vector",
            "source_type": "drskill", "source_reference": "drskill",
            "content_hash": HASH, "local_only": False, "metadata": {}}


def github_entry(**metadata_overrides):
    metadata = {"repo": "friend/pack", "skill_path": "skills/citation",
                "ref": "v1", "directory_hash": GH_HASH}
    metadata.update(metadata_overrides)
    metadata = {k: v for k, v in metadata.items() if v is not None}
    return {"kind": "skill", "selector": "skill:citation", "name": "citation",
            "source_type": "github", "source_reference": "friend/pack@v1",
            "source_version": "v1", "content_hash": "sha256:" + "ab" * 32,
            "local_only": False, "metadata": metadata}


def manifest(entries):
    return {"schema_version": 1, "entries": entries, "harness_mappings": []}


MANIFEST = manifest([hosted_entry(), github_entry()])


class _CodeloadStub(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/friend/pack/tar.gz/"):
            body = repo_tarball()
            self.send_response(200)
        else:
            body = b"nope"
            self.send_response(404)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    monkeypatch.chdir(project)
    service.save_credentials("http://svc.test", "drsk_x")

    server = HTTPServer(("127.0.0.1", 0), _CodeloadStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DRSKILL_CODELOAD_URL",
                       f"http://127.0.0.1:{server.server_address[1]}")

    state = {"manifest": MANIFEST}

    def fake_api_request(method, path, token=None, json_body=None, base_url=None,
                         raw=False, raw_body=None, content_type=None, binary=False):
        if path == "/api/v1/loadouts/drew/pack":
            return {"loadout": {"owner": "drew", "slug": "pack", "name": "Pack",
                                "visibility": "private", "description": None,
                                "published_at": None,
                                "current_revision": {"number": 2, "runtime_hash": "sha256:" + "ee" * 32}}}
        if path == "/api/v1/loadouts/drew/pack/revisions/2":
            return json.dumps(state["manifest"])
        if path == f"/api/v1/content/{HASH}":
            return content.pack(FILES)
        if path == "/api/v1/loadouts/drew/empty":
            return {"loadout": {"owner": "drew", "slug": "empty", "name": "Empty",
                                "visibility": "private", "description": None,
                                "published_at": None, "current_revision": None}}
        raise service.ServiceError("not_found", "Not found.")

    monkeypatch.setattr(service, "api_request", fake_api_request)
    yield home, project, state
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def skills_dir(home):
    return home / ".agents" / "skills"


def test_install_hosted_and_github_entries(env):
    home, _, _ = env
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (skills_dir(home) / "vector" / "SKILL.md").read_bytes() == b"# Vector\n"
    assert (skills_dir(home) / "citation" / "reference" / "tips.md").read_bytes() == b"tips\n"
    assert "friend/pack @ v1" in result.output
    assert "2 installed" in result.output


def test_reinstall_is_a_no_op_for_both_kinds(env):
    home, _, _ = env
    runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    assert result.exit_code == 0, result.output
    assert "vector: already installed" in result.output
    assert "citation: already installed" in result.output
    assert "2 already installed" in result.output


def test_legacy_entry_installs_with_the_caveat(env):
    home, _, state = env
    from drskill import resolution
    legacy = github_entry(directory_hash=None)
    legacy["content_hash"] = resolution.content_hash(GH_SKILL_MD.decode())
    state["manifest"] = manifest([legacy])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    assert result.exit_code == 0, result.output
    assert "bundled files are unverified" in result.output
    assert (skills_dir(home) / "citation" / "SKILL.md").exists()


def test_mismatch_with_yes_fails_that_entry_only(env):
    home, _, state = env
    state["manifest"] = manifest([
        hosted_entry(),
        github_entry(directory_hash="sha256:" + "00" * 32),
    ])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    assert result.exit_code == 0, result.output
    assert "The remote skill has been updated since this loadout was created" in result.output
    assert "Rerun interactively" in result.output
    assert (skills_dir(home) / "vector").exists()
    assert not (skills_dir(home) / "citation").exists()


def test_unparseable_source_is_reported_and_skipped(env):
    home, _, state = env
    broken = github_entry()
    broken["metadata"] = {}
    broken["source_reference"] = "???"
    broken["source_version"] = None
    state["manifest"] = manifest([hosted_entry(), broken])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    assert result.exit_code == 0, result.output
    assert "not fetchable" in result.output
    assert (skills_dir(home) / "vector").exists()


def test_all_entries_failing_exits_one(env):
    home, _, state = env
    state["manifest"] = manifest([github_entry(directory_hash="sha256:" + "00" * 32)])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    assert result.exit_code == 1
    assert not (skills_dir(home) / "citation").exists()


def test_install_prefers_the_project_store_inside_a_project(env):
    home, project, _ = env
    (project / ".git").mkdir()
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (project / ".agents" / "skills" / "vector" / "SKILL.md").exists()
    assert not skills_dir(home).exists()


def test_changed_content_needs_force(env):
    home, _, _ = env
    runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    (skills_dir(home) / "vector" / "SKILL.md").write_bytes(b"edited locally")

    kept = runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    assert kept.exit_code == 0
    assert "--force" in kept.output
    assert (skills_dir(home) / "vector" / "SKILL.md").read_bytes() == b"edited locally"

    replaced = runner.invoke(app, ["loadout", "install", "drew/pack", "--yes", "--force"])
    assert replaced.exit_code == 0, replaced.output
    assert (skills_dir(home) / "vector" / "SKILL.md").read_bytes() == b"# Vector\n"


def test_declining_the_confirmation_writes_nothing(env):
    home, _, _ = env
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="n\n")
    assert result.exit_code == 0
    assert not skills_dir(home).exists()


def test_harness_flag_targets_that_harness_directory(env):
    home, _, _ = env
    result = runner.invoke(
        app, ["loadout", "install", "drew/pack", "--harness", "claude-code", "--yes"])
    assert result.exit_code == 0, result.output
    assert (home / ".claude" / "skills" / "vector" / "SKILL.md").exists()
    assert not skills_dir(home).exists()


def test_loadout_install_bridges_blind_harnesses(env, monkeypatch):
    home, _, _ = env
    (home / ".claude").mkdir()
    monkeypatch.setattr(cli_mod.interactive, "can_interact", lambda *a, **k: None)
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    assert "Claude Code" in result.output
    assert (home / ".claude" / "skills" / "vector").is_symlink()
    assert (home / ".claude" / "skills" / "citation").is_symlink()


def test_loadout_without_a_revision_errors(env):
    result = runner.invoke(app, ["loadout", "install", "drew/empty", "--yes"])
    assert result.exit_code == 1
    assert "no published revision" in result.output


# -- mismatch remediation -----------------------------------------------------

from drskill import cli as cli_mod  # noqa: E402


def keys(*seq):
    it = iter(seq)
    return lambda: next(it)


@pytest.fixture
def remediation(env, monkeypatch):
    """A mismatching github entry plus fakes for identity, publish, and fork."""
    home, project, state = env
    state["manifest"] = manifest([github_entry(directory_hash="sha256:" + "00" * 32)])
    monkeypatch.setattr(cli_mod.interactive, "can_interact", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "_review_fetched", lambda *a, **k: True)

    calls = {"publish": [], "fork": [], "identity": "drew", "fork_fail_once": False}
    real = service.api_request

    def fake(method, path, token=None, json_body=None, base_url=None,
             raw=False, raw_body=None, content_type=None, binary=False):
        if path == "/api/v1/identity":
            return {"user": {"handle": calls["identity"], "display_name": None}}
        if method == "POST" and path.endswith("/fork"):
            if calls["fork_fail_once"]:
                calls["fork_fail_once"] = False
                raise service.ServiceError("loadout_invalid", "The loadout is invalid.",
                                           details={"slug": ["has already been taken"]})
            calls["fork"].append(json_body)
            slug = (json_body or {}).get("loadout", {}).get("slug") or "pack"
            return {"loadout": {"owner": calls["identity"], "slug": slug, "name": "Pack",
                                "visibility": "private", "description": None,
                                "published_at": None, "current_revision": None}}
        if method == "POST" and path.endswith("/revisions"):
            calls["publish"].append({"path": path, "json_body": json_body})
            return {"revision": {"number": 3, "runtime_hash": "sha256:" + "ff" * 32}}
        return real(method, path, token=token, json_body=json_body, base_url=base_url,
                    raw=raw, raw_body=raw_body, content_type=content_type, binary=binary)

    monkeypatch.setattr(service, "api_request", fake)
    return home, state, calls


def test_owner_mismatch_reviews_and_republishes(remediation):
    home, state, calls = remediation
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    assert "The remote skill has been updated" in result.output
    assert len(calls["publish"]) == 1
    assert calls["publish"][0]["path"] == "/api/v1/loadouts/drew/pack/revisions"
    published = calls["publish"][0]["json_body"]["manifest"]
    entry = published["entries"][0]
    assert entry["metadata"]["directory_hash"] == GH_HASH
    assert "runtime_hash" in calls["publish"][0]["json_body"]
    assert (skills_dir(home) / "citation" / "SKILL.md").exists()
    assert "1 installed" in result.output


def test_owner_declining_publish_installs_nothing(remediation):
    home, state, calls = remediation
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="y\nn\n")
    assert result.exit_code == 1
    assert calls["publish"] == []
    assert not (skills_dir(home) / "citation").exists()


def test_review_quit_aborts_the_entry(remediation, monkeypatch):
    home, state, calls = remediation
    monkeypatch.setattr(cli_mod, "_review_fetched", lambda *a, **k: False)
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="y\n")
    assert result.exit_code == 1
    assert calls["publish"] == []


def test_non_owner_forks_then_republishes(remediation):
    home, state, calls = remediation
    calls["identity"] = "me"
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="y\ny\ny\n")
    assert result.exit_code == 0, result.output
    assert "Fork drew/pack" in result.output
    assert len(calls["fork"]) == 1
    assert calls["publish"][0]["path"] == "/api/v1/loadouts/me/pack/revisions"
    assert (skills_dir(home) / "citation" / "SKILL.md").exists()


def test_fork_slug_collision_reprompts(remediation):
    home, state, calls = remediation
    calls["identity"] = "me"
    calls["fork_fail_once"] = True
    result = runner.invoke(app, ["loadout", "install", "drew/pack"],
                           input="y\ny\npack-two\ny\n")
    assert result.exit_code == 0, result.output
    assert calls["fork"][0]["loadout"]["slug"] == "pack-two"
    assert calls["publish"][0]["path"] == "/api/v1/loadouts/me/pack-two/revisions"


def test_review_fetched_runs_lint_and_records_acks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    files = [{"path": "SKILL.md",
              "data": b"---\nname: vague\ndescription: Helps with various tasks.\n---\nbody\n",
              "executable": False}]
    monkeypatch.setattr(cli_mod.interactive, "can_interact", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "key_source", keys("a", "s", "s"))
    assert cli_mod._review_fetched(files, home, name="vague") is True
    ledger_text = (home / ".drskill.toml").read_text()
    assert "[[ack]]" in ledger_text


# -- MCP entries --------------------------------------------------------------

MCP_HASH = "cc" * 32


def mcp_entry(**metadata_overrides):
    metadata = {"server_name": "Notion", "transport": "stdio",
                "command": "npx", "args": ["-y", "notion-mcp"], "url": None,
                "env_names": ["NOTION_TOKEN"], "tools": ["search"]}
    metadata.update(metadata_overrides)
    return {"kind": "mcp", "selector": "mcp:notion", "name": "notion",
            "source_type": "mcp", "source_reference": "npx -y notion-mcp",
            "content_hash": f"sha256:{MCP_HASH}", "local_only": False,
            "metadata": metadata}


def real_mcp_entry():
    # Built through the real pipeline so content_hash matches what a
    # re-parse of the written file computes.
    from pathlib import Path

    from drskill import manifest_build
    from drskill.mcp import _entry_to_server

    server = _entry_to_server(
        "Notion",
        {"command": "npx", "args": ["-y", "notion-mcp"], "env": {"NOTION_TOKEN": "x"}},
        harness="claude-code", scope="project", source=Path("/tmp/.mcp.json"))
    return manifest_build.server_to_entry(server, ["search"])


def test_mcp_entry_installs_into_project_mcp_json(env):
    _, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    data = json.loads((project / ".mcp.json").read_text())
    assert data["mcpServers"]["Notion"] == {
        "command": "npx", "args": ["-y", "notion-mcp"], "env": {"NOTION_TOKEN": ""}}
    assert "fill in env values for: NOTION_TOKEN" in result.output
    assert "scan --mcp-connect" in result.output
    assert "live access" in result.output
    listing, _, rest = result.output.partition("Proceed?")
    assert "npx -y notion-mcp" in listing


def test_mcp_listing_shows_the_http_url_before_confirmation(env):
    _, project, state = env
    state["manifest"] = manifest([mcp_entry(
        server_name="Linear", transport="http", command=None, args=[],
        url="https://mcp.linear.app/sse", env_names=[])])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    listing, _, rest = result.output.partition("Proceed?")
    assert "https://mcp.linear.app/sse" in listing


def test_mcp_reinstall_is_a_no_op(env):
    _, project, state = env
    state["manifest"] = manifest([real_mcp_entry()])
    runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "already installed" in result.output
    assert "1 already installed" in result.output


def real_http_mcp_entry():
    # Same real pipeline as real_mcp_entry(), but an http server carrying
    # env names, so the written config's re-parsed hash can be checked
    # against the entry's content_hash.
    from pathlib import Path

    from drskill import manifest_build
    from drskill.mcp import _entry_to_server

    server = _entry_to_server(
        "Linear",
        {"url": "https://mcp.linear.app/sse", "env": {"LINEAR_TOKEN": "x"}},
        harness="claude-code", scope="project", source=Path("/tmp/.mcp.json"))
    return manifest_build.server_to_entry(server, [])


def test_mcp_http_reinstall_with_env_names_is_a_no_op(env):
    _, project, state = env
    state["manifest"] = manifest([real_http_mcp_entry()])
    runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "already installed" in result.output


def test_mcp_drifted_server_needs_force(env):
    _, project, state = env
    state["manifest"] = manifest([real_mcp_entry()])
    (project / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"Notion": {"command": "other-command"}}}))
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "differs" in result.output
    data = json.loads((project / ".mcp.json").read_text())
    assert data["mcpServers"]["Notion"]["command"] == "other-command"

    result = runner.invoke(
        app, ["loadout", "install", "drew/pack", "--project", "--force"], input="y\n")
    assert result.exit_code == 0, result.output
    data = json.loads((project / ".mcp.json").read_text())
    assert data["mcpServers"]["Notion"]["command"] == "npx"


def test_mcp_user_scope_prints_a_manual_block(env):
    home, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--user"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "1 manual" in result.output
    assert '"command": "npx"' in result.output
    assert not (project / ".mcp.json").exists()


def test_mixed_manifest_installs_both_kinds(env):
    home, project, state = env
    state["manifest"] = manifest([hosted_entry(), mcp_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (project / ".agents" / "skills" / "vector" / "SKILL.md").exists()
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"]["Notion"]
    assert "2 installed" in result.output


def test_mcp_only_manifest_gets_a_server_header(env):
    _, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Install 1 MCP server:" in result.output
    assert "skills into" not in result.output


def test_mixed_manifest_header_counts_only_skills(env):
    _, project, state = env
    state["manifest"] = manifest([hosted_entry(), mcp_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Install 1 skill into" in result.output


def test_malformed_mcp_metadata_fails_that_entry_only(env):
    _, project, state = env
    bad = mcp_entry()
    bad["metadata"] = "surprise"
    state["manifest"] = manifest([hosted_entry(), bad])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "invalid" in result.output
    assert "1 installed" in result.output
    assert "1 failed" in result.output
    assert not (project / ".mcp.json").exists()


def test_codex_harness_appends_to_config_toml(env):
    home, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    (project / ".codex").mkdir()
    result = runner.invoke(
        app, ["loadout", "install", "drew/pack", "--harness", "codex"], input="y\n")
    assert result.exit_code == 0, result.output
    text = (home / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.Notion]" in text
    assert "1 installed" in result.output


def test_codex_http_entry_prints_a_manual_block(env):
    home, project, state = env
    entry = mcp_entry(transport="http", command=None, args=[],
                      url="https://mcp.example.com/sse", env_names=[])
    state["manifest"] = manifest([entry])
    (project / ".codex").mkdir()
    result = runner.invoke(
        app, ["loadout", "install", "drew/pack", "--harness", "codex"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "1 manual" in result.output
    assert not (home / ".codex" / "config.toml").exists()


# -- pins ----------------------------------------------------------------


def test_install_writes_pins_for_skills(env):
    home, project, state = env
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    from drskill import pins as pins_mod
    pinned = pins_mod.load_pins(project)
    assert ".agents/skills/vector" in pinned
    vec = pinned[".agents/skills/vector"]
    assert vec.loadout == "drew/pack"
    assert vec.revision == 2
    assert vec.selector == "skill:vector"
    assert ".agents/skills/citation" in pinned


def test_reinstall_rebinds_unchanged_entries(env):
    home, project, state = env
    runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    from drskill import pins as pins_mod
    pins_mod.pins_path(project).unlink()
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert ".agents/skills/vector" in pins_mod.load_pins(project)


def test_user_scope_install_pins_under_home(env):
    home, project, state = env
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--user"], input="y\n")
    assert result.exit_code == 0, result.output
    from drskill import pins as pins_mod
    assert ".agents/skills/vector" in pins_mod.load_pins(home)
    assert pins_mod.load_pins(project) == {}


def test_mcp_entries_do_not_pin(env):
    home, project, state = env
    state["manifest"] = manifest([mcp_entry()])
    runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    from drskill import pins as pins_mod
    assert pins_mod.load_pins(project) == {}


def command_entry():
    return {"kind": "command", "selector": "command:hello", "name": "hello",
            "source_type": "drskill", "source_reference": "drskill",
            "content_hash": HASH, "local_only": False, "metadata": {}}


def test_non_skill_kind_entries_are_not_pinned(env):
    home, project, state = env
    state["manifest"] = manifest([command_entry()])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--project"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (project / ".agents" / "skills" / "hello").exists()
    from drskill import pins as pins_mod
    assert pins_mod.load_pins(project) == {}


def test_bridge_retarget_pins_under_the_discovered_root(env, monkeypatch, tmp_path):
    # cwd can sit inside a harness's own store, in which case the real
    # project root is whatever bridge.retarget_cwd discovers, not cwd.
    home, project, state = env
    from drskill import bridge

    discovered = tmp_path / "discovered"
    discovered.mkdir()
    monkeypatch.setattr(bridge, "retarget_cwd",
                        lambda cwd: (discovered, discovered / ".agents" / "skills"))
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="y\n")
    assert result.exit_code == 0, result.output
    from drskill import pins as pins_mod
    pinned = pins_mod.load_pins(discovered)
    assert ".agents/skills/vector" in pinned
    assert pins_mod.load_pins(project) == {}
    assert pins_mod.load_pins(home) == {}
