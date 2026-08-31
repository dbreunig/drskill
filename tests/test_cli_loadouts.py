import json

import pytest
from typer.testing import CliRunner

from drskill import service
from drskill.cli import app

runner = CliRunner()


@pytest.fixture
def signed_in(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    service.save_credentials("http://svc.test", "drsk_x")


@pytest.fixture
def api(monkeypatch):
    calls = []

    def fake_api_request(method, path, token=None, json_body=None, base_url=None, raw=False):
        calls.append({"method": method, "path": path, "token": token,
                      "json_body": json_body, "base_url": base_url, "raw": raw})
        return fake_api_request.response

    fake_api_request.response = {}
    monkeypatch.setattr(service, "api_request", fake_api_request)
    return calls, fake_api_request


LOADOUT = {
    "owner": "drew", "slug": "textbook", "name": "Textbook", "description": None,
    "visibility": "private", "published_at": None,
    "current_revision": {"number": 3, "runtime_hash": "sha256:" + "ab" * 32},
}


def test_list_renders_a_table(signed_in, api):
    calls, fake = api
    fake.response = {"loadouts": [LOADOUT]}
    result = runner.invoke(app, ["loadout", "list"])
    assert result.exit_code == 0
    assert "drew/textbook" in result.output
    assert "#3" in result.output
    assert calls[0]["path"] == "/api/v1/loadouts"
    assert calls[0]["base_url"] == "http://svc.test"


def test_list_json_emits_the_raw_response(signed_in, api):
    calls, fake = api
    fake.response = {"loadouts": [LOADOUT]}
    result = runner.invoke(app, ["loadout", "list", "--json"])
    assert json.loads(result.output)["loadouts"][0]["slug"] == "textbook"


def test_list_when_signed_out_hints_at_login(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    result = runner.invoke(app, ["loadout", "list"])
    assert result.exit_code == 1
    assert "drskill login" in result.output


def test_create_posts_and_prints_the_ref(signed_in, api):
    calls, fake = api
    fake.response = {"loadout": LOADOUT}
    result = runner.invoke(
        app, ["loadout", "create", "textbook", "--name", "Textbook", "--description", "d"]
    )
    assert result.exit_code == 0
    assert "drew/textbook" in result.output
    assert calls[0]["method"] == "POST"
    assert calls[0]["json_body"] == {"loadout": {"slug": "textbook", "name": "Textbook", "description": "d"}}


def test_create_validation_failure_prints_details(signed_in, monkeypatch):
    def failing(*args, **kwargs):
        raise service.ServiceError("loadout_invalid", "The loadout is invalid.",
                                   details={"slug": ["is invalid"]})

    monkeypatch.setattr(service, "api_request", failing)
    result = runner.invoke(app, ["loadout", "create", "Bad Slug", "--name", "X"])
    assert result.exit_code == 1
    assert "The loadout is invalid." in result.output
    assert "slug: is invalid" in result.output


def test_show_prints_metadata_and_provenance(signed_in, api):
    calls, fake = api
    fake.response = {"loadout": {**LOADOUT, "forked_from": {"owner": "ann", "slug": "orig", "revision_number": 2}}}
    result = runner.invoke(app, ["loadout", "show", "drew/textbook"])
    assert result.exit_code == 0
    assert "drew/textbook" in result.output
    assert "Forked from ann/orig" in result.output
    assert calls[0]["path"] == "/api/v1/loadouts/drew/textbook"


def test_show_rejects_a_bad_ref(signed_in, api):
    result = runner.invoke(app, ["loadout", "show", "no-slash"])
    assert result.exit_code == 1
    assert "owner/slug" in result.output
