import json

import pytest
from typer.testing import CliRunner

from drskill import cli as cli_mod, content, service
from drskill.cli import app

runner = CliRunner()


def keys(*seq):
    it = iter(seq)
    return lambda: next(it)


def write_skill_dir(tmp_path, description="Use when demonstrating gated publishing to the registry."):
    d = tmp_path / "proj" / "my-skill"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: my-skill\ndescription: {description}\n---\nBody.\n")
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    service.save_credentials("http://svc.test", "drsk_x")

    state = {"publishes": [], "uploads": [], "existed": False}

    def fake_upload(files, token, base_url):
        state["uploads"].append(files)
        return {"content_hash": content.manifest_hash(files), "uploaded": True}

    monkeypatch.setattr(content, "upload", fake_upload)

    def fake_api_request(method, path, token=None, json_body=None, base_url=None,
                         raw=False, raw_body=None, content_type=None, binary=False):
        if method == "POST" and path == "/api/v1/skills":
            state["publishes"].append(json_body)
            slug = json_body["skill"]["slug"]
            return {"skill": {"owner": "drew", "slug": slug, "visibility": "private",
                              "description": None,
                              "current_version": {"number": 1, "content_hash": json_body["skill"]["content_hash"]}},
                    "version": {"number": 1, "content_hash": json_body["skill"]["content_hash"],
                                "note": json_body["skill"].get("note")},
                    "existed": state["existed"]}
        raise service.ServiceError("not_found", "Not found.")

    monkeypatch.setattr(service, "api_request", fake_api_request)
    return home, state


def test_clean_skill_publishes(env, tmp_path):
    home, state = env
    d = write_skill_dir(tmp_path)
    result = runner.invoke(app, ["skill", "publish", str(d), "-m", "first cut"])
    assert result.exit_code == 0, result.output
    assert "Published drew/my-skill@1" in result.output
    assert len(state["uploads"]) == 1
    body = state["publishes"][0]["skill"]
    assert body["slug"] == "my-skill"
    assert body["name"] == "my-skill"
    assert body["note"] == "first cut"
    assert body["content_hash"].startswith("sha256:")


def test_idempotent_republish_reports_existing(env, tmp_path):
    home, state = env
    state["existed"] = True
    d = write_skill_dir(tmp_path)
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 0, result.output
    assert "already" in result.output


def test_blocking_findings_stop_a_non_interactive_publish(env, tmp_path):
    home, state = env
    d = write_skill_dir(tmp_path, description="Helps with various tasks.")  # generic-description
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 1
    assert "generic-description" in result.output or "distinguishing" in result.output
    assert state["uploads"] == []
    assert state["publishes"] == []


def test_interactive_acks_unblock_the_publish(env, tmp_path, monkeypatch):
    home, state = env
    d = write_skill_dir(tmp_path, description="Helps with various tasks.")
    monkeypatch.setattr(cli_mod.interactive, "can_interact", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "key_source", keys("a", "a", "a"))
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 0, result.output
    assert len(state["publishes"]) == 1
    assert "[[ack]]" in (home / ".drskill.toml").read_text()


def test_quitting_the_review_publishes_nothing(env, tmp_path, monkeypatch):
    home, state = env
    d = write_skill_dir(tmp_path, description="Helps with various tasks.")
    monkeypatch.setattr(cli_mod.interactive, "can_interact", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "key_source", keys("q"))
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 1
    assert state["publishes"] == []


def test_missing_skill_md_errors(env, tmp_path):
    d = tmp_path / "not-a-skill"
    d.mkdir()
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 1
    assert "SKILL.md" in result.output
