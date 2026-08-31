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
    assert result.exit_code == 0
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


def test_create_defaults_the_name_from_the_slug(signed_in, api):
    calls, fake = api
    fake.response = {"loadout": LOADOUT}
    result = runner.invoke(app, ["loadout", "create", "textbook-pack"])
    assert result.exit_code == 0
    assert calls[0]["json_body"] == {"loadout": {"slug": "textbook-pack", "name": "Textbook Pack"}}


def test_create_validation_failure_prints_details(signed_in, monkeypatch):
    def failing(*args, **kwargs):
        raise service.ServiceError("loadout_invalid", "The loadout is invalid.",
                                   details={"slug": ["is invalid"]})

    monkeypatch.setattr(service, "api_request", failing)
    result = runner.invoke(app, ["loadout", "create", "Bad Slug", "--name", "X"])
    assert result.exit_code == 1
    assert "The loadout is invalid." in result.output
    assert "slug: is invalid" in result.output


def test_create_validation_failure_handles_non_list_detail_values(signed_in, monkeypatch):
    def failing(*args, **kwargs):
        raise service.ServiceError(
            "loadout_invalid", "The loadout is invalid.", details={"slug": "is invalid"}
        )

    monkeypatch.setattr(service, "api_request", failing)
    result = runner.invoke(app, ["loadout", "create", "Bad Slug", "--name", "X"])
    assert result.exit_code == 1
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
    calls, fake = api
    result = runner.invoke(app, ["loadout", "show", "no-slash"])
    assert result.exit_code == 1
    assert "owner/slug" in result.output
    assert calls == []


def test_revisions_renders_a_table(signed_in, api):
    calls, fake = api
    fake.response = {"revisions": [
        {"number": 2, "runtime_hash": "sha256:" + "cd" * 32, "published_at": "2026-08-31T00:00:00Z",
         "reproducible": True, "schema_version": 1},
        {"number": 1, "runtime_hash": "sha256:" + "ab" * 32, "published_at": "2026-08-30T00:00:00Z",
         "reproducible": False, "schema_version": 1},
    ]}
    result = runner.invoke(app, ["loadout", "revisions", "drew/textbook"])
    assert result.exit_code == 0
    assert "2" in result.output and "1" in result.output
    assert calls[0]["path"] == "/api/v1/loadouts/drew/textbook/revisions"


def test_publish_sends_the_computed_hash(signed_in, api, tmp_path):
    calls, fake = api
    fake.response = {"revision": {"number": 1, "runtime_hash": "sha256:" + "ee" * 32}}
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text('{"schema_version":1,"entries":[],"harness_mappings":[]}')

    result = runner.invoke(app, ["loadout", "publish", "drew/textbook", str(manifest_path)])
    assert result.exit_code == 0
    assert "Published revision 1" in result.output
    body = calls[0]["json_body"]
    _, expected_hash = service.canonical_manifest(json.loads(manifest_path.read_text()))
    assert body["runtime_hash"] == expected_hash
    assert body["manifest"]["schema_version"] == 1


def test_publish_no_verify_omits_the_hash(signed_in, api, tmp_path):
    calls, fake = api
    fake.response = {"revision": {"number": 1, "runtime_hash": "sha256:" + "ee" * 32}}
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text('{"schema_version":1,"entries":[],"harness_mappings":[]}')
    runner.invoke(app, ["loadout", "publish", "drew/textbook", str(manifest_path), "--no-verify"])
    assert "runtime_hash" not in calls[0]["json_body"]


def test_publish_hash_mismatch_prints_both_hashes(signed_in, monkeypatch, tmp_path):
    def failing(*args, **kwargs):
        raise service.ServiceError(
            "revision_invalid", "The revision manifest is invalid.",
            details={"manifest": ["runtime_hash mismatch: client sent x, server computed y"]},
        )

    monkeypatch.setattr(service, "api_request", failing)
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text('{"schema_version":1,"entries":[],"harness_mappings":[]}')
    result = runner.invoke(app, ["loadout", "publish", "drew/textbook", str(manifest_path)])
    assert result.exit_code == 1
    assert "runtime_hash mismatch" in result.output
    assert "client runtime_hash: sha256:" in result.output


def test_publish_rejects_an_unreadable_or_invalid_manifest(signed_in, api, tmp_path):
    result = runner.invoke(app, ["loadout", "publish", "drew/textbook", str(tmp_path / "missing.json")])
    assert result.exit_code == 1
    assert "Could not read manifest" in result.output

    bad = tmp_path / "bad.json"
    bad.write_text("[1,2]")
    result = runner.invoke(app, ["loadout", "publish", "drew/textbook", str(bad)])
    assert result.exit_code == 1
    assert "JSON object" in result.output


def test_fetch_by_number_prints_the_raw_document(signed_in, api):
    calls, fake = api
    fake.response = '{"a":1}'
    result = runner.invoke(app, ["loadout", "fetch", "drew/textbook", "3"])
    assert result.exit_code == 0
    assert '{"a":1}' in result.output
    assert calls[0]["path"] == "/api/v1/loadouts/drew/textbook/revisions/3"
    assert calls[0]["raw"] is True


def test_fetch_bare_hash_uses_the_global_lookup(signed_in, api):
    calls, fake = api
    fake.response = '{"a":1}'
    target = "sha256:" + "ab" * 32
    result = runner.invoke(app, ["loadout", "fetch", target])
    assert result.exit_code == 0
    assert calls[0]["path"] == f"/api/v1/revision_hashes/{target}"


def test_fetch_ref_without_revision_errors(signed_in, api):
    result = runner.invoke(app, ["loadout", "fetch", "drew/textbook"])
    assert result.exit_code == 1
    assert "revision number" in result.stderr
    assert "revision number" not in result.stdout


def test_fetch_service_error_writes_to_stderr(signed_in, monkeypatch):
    def failing(*args, **kwargs):
        raise service.ServiceError("not_found", "Revision not found.")

    monkeypatch.setattr(service, "api_request", failing)
    result = runner.invoke(app, ["loadout", "fetch", "drew/textbook", "3"])
    assert result.exit_code == 1
    assert "Revision not found." in result.stderr
    assert "Revision not found." not in result.stdout


def test_fetch_output_writes_the_file(signed_in, api, tmp_path):
    calls, fake = api
    fake.response = '{"a":1}'
    out = tmp_path / "manifest.json"
    result = runner.invoke(app, ["loadout", "fetch", "drew/textbook", "3", "-o", str(out)])
    assert result.exit_code == 0
    assert out.read_text() == '{"a":1}'


def test_fetch_output_writes_bytes_with_non_ascii_content(signed_in, api, tmp_path):
    calls, fake = api
    fake.response = '{"name":"café"}'
    out = tmp_path / "manifest.json"
    result = runner.invoke(app, ["loadout", "fetch", "drew/textbook", "3", "-o", str(out)])
    assert result.exit_code == 0
    assert out.read_bytes() == '{"name":"café"}'.encode()


def test_fetch_output_write_failure_reports_to_stderr(signed_in, api, tmp_path):
    calls, fake = api
    fake.response = '{"a":1}'
    out = tmp_path / "missing-dir" / "manifest.json"
    result = runner.invoke(app, ["loadout", "fetch", "drew/textbook", "3", "-o", str(out)])
    assert result.exit_code == 1
    assert "Could not write" in result.stderr
    assert "Could not write" not in result.stdout
