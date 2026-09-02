import json

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

MANIFEST = {
    "schema_version": 1,
    "entries": [
        {"kind": "skill", "selector": "skill:vector", "name": "vector",
         "source_type": "drskill", "source_reference": "drskill",
         "content_hash": HASH, "local_only": False, "metadata": {}},
        {"kind": "skill", "selector": "skill:tracked", "name": "tracked",
         "source_type": "github", "source_reference": "friend/tracked@v1",
         "content_hash": "sha256:" + "ab" * 32, "local_only": False, "metadata": {}},
    ],
    "harness_mappings": [],
}


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

    def fake_api_request(method, path, token=None, json_body=None, base_url=None,
                         raw=False, raw_body=None, content_type=None, binary=False):
        if path == "/api/v1/loadouts/drew/pack":
            return {"loadout": {"owner": "drew", "slug": "pack", "name": "Pack",
                                "visibility": "private", "description": None,
                                "published_at": None,
                                "current_revision": {"number": 2, "runtime_hash": "sha256:" + "ee" * 32}}}
        if path == "/api/v1/loadouts/drew/pack/revisions/2":
            return json.dumps(MANIFEST)
        if path == f"/api/v1/content/{HASH}":
            return content.pack(FILES)
        if path == "/api/v1/loadouts/drew/empty":
            return {"loadout": {"owner": "drew", "slug": "empty", "name": "Empty",
                                "visibility": "private", "description": None,
                                "published_at": None, "current_revision": None}}
        raise service.ServiceError("not_found", "Not found.")

    monkeypatch.setattr(service, "api_request", fake_api_request)
    return home, project


def installed_dir(home):
    return home / ".agents" / "skills" / "vector"


def test_install_into_the_shared_user_store_by_default(env):
    home, project = env
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (installed_dir(home) / "SKILL.md").read_bytes() == b"# Vector\n"
    assert (installed_dir(home) / "scripts" / "run.sh").stat().st_mode & 0o111
    assert str(home / ".agents" / "skills") in result.output
    assert "1 entr" in result.output and "external" in result.output  # skipped github entry


def test_install_prefers_the_project_store_inside_a_project(env):
    home, project = env
    (project / ".git").mkdir()
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (project / ".agents" / "skills" / "vector" / "SKILL.md").exists()
    assert not installed_dir(home).exists()


def test_reinstalling_identical_content_is_a_no_op(env):
    home, _ = env
    runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    result = runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    assert result.exit_code == 0, result.output
    assert "already installed" in result.output


def test_changed_content_needs_force(env):
    home, _ = env
    runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    (installed_dir(home) / "SKILL.md").write_bytes(b"edited locally")

    kept = runner.invoke(app, ["loadout", "install", "drew/pack", "--yes"])
    assert kept.exit_code == 0
    assert "--force" in kept.output
    assert (installed_dir(home) / "SKILL.md").read_bytes() == b"edited locally"

    replaced = runner.invoke(app, ["loadout", "install", "drew/pack", "--yes", "--force"])
    assert replaced.exit_code == 0, replaced.output
    assert (installed_dir(home) / "SKILL.md").read_bytes() == b"# Vector\n"


def test_declining_the_confirmation_writes_nothing(env):
    home, _ = env
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="n\n")
    assert result.exit_code == 0
    assert not installed_dir(home).exists()


def test_harness_flag_targets_that_harness_directory(env):
    home, _ = env
    result = runner.invoke(
        app, ["loadout", "install", "drew/pack", "--harness", "claude-code", "--yes"])
    assert result.exit_code == 0, result.output
    assert (home / ".claude" / "skills" / "vector" / "SKILL.md").exists()
    assert not installed_dir(home).exists()


def test_shared_default_warns_about_harnesses_that_cannot_see_it(env):
    home, _ = env
    (home / ".claude").mkdir()  # detect marker for claude-code
    result = runner.invoke(app, ["loadout", "install", "drew/pack"], input="n\n")
    assert "Claude Code" in result.output
    assert "--harness" in result.output


def test_loadout_without_a_revision_errors(env):
    result = runner.invoke(app, ["loadout", "install", "drew/empty", "--yes"])
    assert result.exit_code == 1
    assert "no published revision" in result.output
