import json

import pytest
from typer.testing import CliRunner

from drskill import cli as cli_mod, content, service
from drskill.cli import app
from drskill.models import Contributor, Provenance, TokenCost
from drskill.resolution import World

runner = CliRunner()

OLD_FILES = [{"path": "SKILL.md", "data": b"old\n", "executable": False}]
NEW_FILES = [{"path": "SKILL.md", "data": b"new\n", "executable": False}]
OLD_HASH = content.manifest_hash(OLD_FILES)
NEW_HASH = content.manifest_hash(NEW_FILES)


def contributor(name):
    return Contributor(
        id=f"/tmp/{name}", kind="skill", name=name,
        source=Provenance(kind="unmanaged", source=None), scope="project",
        token_cost=TokenCost(catalog_tokens=1, body_tokens=1),
        content_hash="sha256:" + "ab" * 32,
    )


def hosted_entry(name, content_hash):
    return {"kind": "skill", "selector": f"skill:{name}", "name": name,
            "source_type": "drskill", "source_reference": "drskill",
            "content_hash": content_hash, "local_only": False, "metadata": {}}


def github_entry(name, directory_hash):
    return {"kind": "skill", "selector": f"skill:{name}", "name": name,
            "source_type": "github", "source_reference": "friend/pack",
            "content_hash": "sha256:" + "ab" * 32, "local_only": False,
            "metadata": {"repo": "friend/pack", "skill_path": "sk", "ref": "v1",
                         "files": ["SKILL.md"], "directory_hash": directory_hash}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    service.save_credentials("http://svc.test", "drsk_x")
    monkeypatch.setattr(content, "collect_files", lambda c: list(NEW_FILES))
    monkeypatch.setattr(cli_mod, "_review_fetched", lambda *a, **k: True)

    state = {
        "manifest": {"schema_version": 1,
                     "entries": [hosted_entry("alpha", OLD_HASH),
                                 hosted_entry("beta", NEW_HASH)],
                     "harness_mappings": []},
        "identity": "drew",
        "publish": [],
        "uploads": [],
    }

    def fake_upload(files, token, base_url):
        state["uploads"].append(files)
        return {"content_hash": content.manifest_hash(files), "uploaded": True}

    monkeypatch.setattr(content, "upload", fake_upload)

    def fake_api_request(method, path, token=None, json_body=None, base_url=None,
                         raw=False, raw_body=None, content_type=None, binary=False):
        if path == "/api/v1/identity":
            return {"user": {"handle": state["identity"], "display_name": None}}
        if path == "/api/v1/loadouts/drew/pack":
            return {"loadout": {"owner": "drew", "slug": "pack", "name": "Pack",
                                "visibility": "private", "description": None,
                                "published_at": None,
                                "current_revision": {"number": 2, "runtime_hash": "sha256:" + "ee" * 32}}}
        if path == "/api/v1/loadouts/drew/pack/revisions/2":
            return json.dumps(state["manifest"])
        if method == "POST" and path == "/api/v1/loadouts/drew/pack/revisions":
            state["publish"].append(json_body)
            return {"revision": {"number": 3, "runtime_hash": "sha256:" + "ff" * 32}}
        raise service.ServiceError("not_found", "Not found.")

    monkeypatch.setattr(service, "api_request", fake_api_request)
    monkeypatch.setattr(cli_mod, "run_scan",
        lambda *a, **k: (World(contributors={c.id: c for c in state["world"]}), []))
    state["world"] = [contributor("alpha"), contributor("beta")]
    return state


def test_changed_hosted_entry_uploads_and_republishes(env):
    result = runner.invoke(app, ["loadout", "update", "drew/pack"], input="y\n")
    assert result.exit_code == 0, result.output
    assert len(env["uploads"]) == 1
    published = env["publish"][0]["manifest"]
    by_name = {e["name"]: e for e in published["entries"]}
    assert by_name["alpha"]["content_hash"] == NEW_HASH
    assert by_name["beta"] == hosted_entry("beta", NEW_HASH)
    assert "runtime_hash" in env["publish"][0]
    assert "Published revision 3" in result.output


def test_changed_github_entry_refreshes_metadata_only(env):
    env["manifest"]["entries"] = [github_entry("alpha", OLD_HASH)]
    env["world"] = [contributor("alpha")]
    result = runner.invoke(app, ["loadout", "update", "drew/pack", "--yes"])
    assert result.exit_code == 0, result.output
    md = env["publish"][0]["manifest"]["entries"][0]["metadata"]
    assert md["directory_hash"] == NEW_HASH
    assert md["files"] == ["SKILL.md"]
    assert md["repo"] == "friend/pack" and md["skill_path"] == "sk" and md["ref"] == "v1"
    assert env["uploads"] == []


def test_up_to_date_short_circuits(env):
    env["manifest"]["entries"] = [hosted_entry("beta", NEW_HASH)]
    result = runner.invoke(app, ["loadout", "update", "drew/pack", "--yes"])
    assert result.exit_code == 0
    assert "Already up to date." in result.output
    assert env["publish"] == []


def test_missing_entries_are_left_as_published(env):
    env["manifest"]["entries"] = [hosted_entry("alpha", OLD_HASH),
                                  hosted_entry("gone", OLD_HASH)]
    result = runner.invoke(app, ["loadout", "update", "drew/pack", "--yes"])
    assert result.exit_code == 0, result.output
    assert "gone: missing locally; left as published" in result.output
    by_name = {e["name"]: e for e in env["publish"][0]["manifest"]["entries"]}
    assert by_name["gone"]["content_hash"] == OLD_HASH


def test_non_owner_is_refused(env):
    env["identity"] = "someone-else"
    result = runner.invoke(app, ["loadout", "update", "drew/pack", "--yes"])
    assert result.exit_code == 1
    assert "fork it first" in result.output
    assert env["publish"] == []


def test_review_abort_publishes_nothing(env, monkeypatch):
    monkeypatch.setattr(cli_mod, "_review_fetched", lambda *a, **k: False)
    result = runner.invoke(app, ["loadout", "update", "drew/pack", "--yes"])
    assert result.exit_code == 1
    assert env["publish"] == []


def test_declining_the_confirm_publishes_nothing(env):
    result = runner.invoke(app, ["loadout", "update", "drew/pack"], input="n\n")
    assert result.exit_code == 0
    assert env["publish"] == []


def test_review_fetched_skips_the_ack_loop_when_not_interactive(tmp_path, monkeypatch):
    # Findings exist, stdin is not a tty, and no keys are fed: the review
    # must print findings and return True instead of blocking on keypresses.
    home = tmp_path / "home"
    home.mkdir()
    files = [{"path": "SKILL.md",
              "data": b"---\nname: vague\ndescription: Helps with various tasks.\n---\nbody\n",
              "executable": False}]
    monkeypatch.setattr(cli_mod, "key_source",
        lambda: (_ for _ in ()).throw(AssertionError("key_source called")))
    monkeypatch.setattr(cli_mod.interactive, "can_interact", lambda *a, **k: "no tty")
    assert cli_mod._review_fetched(files, home, name="vague") is True
    assert not (home / ".drskill.toml").exists()
