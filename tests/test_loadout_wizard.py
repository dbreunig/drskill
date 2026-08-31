import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drskill import loadout_wizard, pipeline, service
from drskill.cli import app
from drskill.models import Contributor, Deployment, Provenance, TokenCost
from drskill.resolution import World

runner = CliRunner()


def contributor(name, scope="project", prov_kind="gh-skill", source="friend/x@v1",
                harnesses=("claude-code",), system=False):
    return Contributor(
        id=f"/tmp/{name}",
        kind="skill",
        name=name,
        source=Provenance(kind=prov_kind, source=source),
        scope=scope,
        deployments=[
            Deployment(harness=h, path=Path(f"/tmp/{name}"), scope=scope,
                       via_symlink=False, order=0)
            for h in harnesses
        ],
        token_cost=TokenCost(catalog_tokens=1, body_tokens=1),
        content_hash="sha256:" + "ab" * 32,
        system=system,
    )


def make_world(*contributors):
    return World(contributors={c.id: c for c in contributors})


@pytest.fixture
def wizard_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    service.save_credentials("http://svc.test", "drsk_x")
    monkeypatch.setattr(loadout_wizard, "_stdin_is_tty", lambda: True)

    calls = []

    def fake_api_request(method, path, token=None, json_body=None, base_url=None, raw=False):
        calls.append({"method": method, "path": path, "json_body": json_body,
                      "base_url": base_url})
        if path == "/api/v1/loadouts":
            slug = json_body["loadout"]["slug"]
            return {"loadout": {"owner": "drew", "slug": slug, "name": json_body["loadout"]["name"],
                                "visibility": "private", "description": None,
                                "published_at": None, "current_revision": None}}
        return {"revision": {"number": 1, "runtime_hash": "sha256:" + "ee" * 32}}

    monkeypatch.setattr(service, "api_request", fake_api_request)
    return calls


def set_world(monkeypatch, world):
    monkeypatch.setattr(pipeline, "run_scan", lambda *a, **kw: (world, []))


def test_wizard_publishes_the_confirmed_selection(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha"), contributor("beta")))

    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"],
                           input="\ny\n")  # accept preselection, confirm
    assert result.exit_code == 0, result.output
    assert "Published revision 1" in result.output
    create_call, publish_call = calls
    assert create_call["path"] == "/api/v1/loadouts"
    assert publish_call["path"] == "/api/v1/loadouts/drew/pack/revisions"
    names = {entry["name"] for entry in publish_call["json_body"]["manifest"]["entries"]}
    assert names == {"alpha", "beta"}
    assert "runtime_hash" in publish_call["json_body"]


def test_toggling_removes_an_entry(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha"), contributor("beta")))

    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"],
                           input="2\n\ny\n")  # toggle #2 off, accept, confirm
    assert result.exit_code == 0, result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert [entry["name"] for entry in entries] == ["alpha"]


def test_sections_and_preselection(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(
        contributor("proj-skill", scope="project"),
        contributor("user-skill", scope="user"),
    ))
    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"],
                           input="\nn\n")  # accept, then decline confirm
    output = result.output
    assert output.index("Project scope") < output.index("User scope")
    assert "[x] 1" in output.replace("  ", " ") or "[x]" in output.split("proj-skill")[0].rsplit("\n", 1)[-1]
    # user-scope rows start unselected
    before_user = output.split("user-skill")[0].rsplit("\n", 1)[-1]
    assert "[ ]" in before_user


def test_user_scope_is_unselected_by_default(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("proj-skill", scope="project"),
        contributor("user-skill", scope="user"),
    ))
    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"],
                           input="\ny\n")
    assert result.exit_code == 0, result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert [entry["name"] for entry in entries] == ["proj-skill"]


def test_harness_filter_and_badges(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("cc-only", harnesses=("claude-code",)),
        contributor("pi-only", harnesses=("pi",)),
        contributor("both", harnesses=("claude-code", "pi")),
    ))
    result = runner.invoke(
        app, ["loadout", "create", "pack", "--from-project", "--harness", "claude-code"],
        input="\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "pi-only" not in result.output
    assert "[claude-code, pi]" in result.output
    names = {entry["name"] for entry in calls[1]["json_body"]["manifest"]["entries"]}
    assert names == {"cc-only", "both"}


def test_system_contributors_are_skipped(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(contributor("vendored", system=True)))
    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"], input="")
    assert result.exit_code == 1
    assert "No skills found" in result.output


def test_local_only_warning_in_summary(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(contributor("untracked", prov_kind="unmanaged", source=None)))
    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"],
                           input="\ny\n")
    assert "local-only" in result.output
    assert "blocks making this loadout public" in result.output


def test_decline_at_confirm_makes_no_server_calls(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha")))
    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"],
                           input="\nn\n")
    assert result.exit_code == 0
    assert calls == []


def test_zero_selection_exits(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha")))
    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"],
                           input="n\n\n")  # clear all, accept
    assert result.exit_code == 1
    assert "Nothing selected" in result.output
    assert calls == []


def test_publish_failure_reports_created_but_empty(wizard_env, monkeypatch, tmp_path):
    set_world(monkeypatch, make_world(contributor("alpha")))

    def failing_api(method, path, token=None, json_body=None, base_url=None, raw=False):
        if path == "/api/v1/loadouts":
            return {"loadout": {"owner": "drew", "slug": "pack", "name": "Pack",
                                "visibility": "private", "description": None,
                                "published_at": None, "current_revision": None}}
        raise service.ServiceError("revision_invalid", "The revision manifest is invalid.",
                                   details={"manifest": ["boom"]})

    monkeypatch.setattr(service, "api_request", failing_api)
    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"],
                           input="\ny\n")
    assert result.exit_code == 1
    assert "Created drew/pack, but the publish failed" in result.output
    assert "drskill loadout publish drew/pack" in result.output
    saved = [token for token in result.output.split() if token.endswith(".json")]
    assert saved, result.output
    manifest = json.loads(Path(saved[-1]).read_text(encoding="utf-8"))
    assert manifest["entries"][0]["name"] == "alpha"


def test_manifest_out_writes_the_manifest(wizard_env, monkeypatch, tmp_path):
    set_world(monkeypatch, make_world(contributor("alpha")))
    out = tmp_path / "m.json"
    result = runner.invoke(
        app, ["loadout", "create", "pack", "--from-project", "--manifest-out", str(out)],
        input="\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 1


def test_manifest_out_not_written_on_decline(wizard_env, monkeypatch, tmp_path):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha")))
    out = tmp_path / "m.json"
    result = runner.invoke(
        app, ["loadout", "create", "pack", "--from-project", "--manifest-out", str(out)],
        input="\nn\n",
    )
    assert result.exit_code == 0, result.output
    assert not out.exists()
    assert calls == []


def test_select_all_includes_user_scope(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("proj-skill", scope="project"),
        contributor("user-skill", scope="user"),
    ))
    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"],
                           input="a\n\ny\n")  # select all, accept, confirm
    assert result.exit_code == 0, result.output
    names = {entry["name"] for entry in calls[1]["json_body"]["manifest"]["entries"]}
    assert names == {"proj-skill", "user-skill"}


def test_create_failure_stops_before_publish(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(contributor("alpha")))
    calls = []

    def failing_create(method, path, token=None, json_body=None, base_url=None, raw=False):
        calls.append(path)
        raise service.ServiceError(
            "loadout_invalid", "The loadout is invalid.", details={"slug": ["is invalid"]}
        )

    monkeypatch.setattr(service, "api_request", failing_create)
    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"],
                           input="\ny\n")
    assert result.exit_code == 1
    assert "The loadout is invalid." in result.output
    assert "is invalid" in result.output
    assert calls == ["/api/v1/loadouts"]


def test_unknown_harness_through_wizard(wizard_env):
    result = runner.invoke(
        app, ["loadout", "create", "pack", "--from-project", "--harness", "nope"],
    )
    assert result.exit_code == 1
    assert "unknown harness" in result.output
    assert "valid ids" in result.output


def test_non_tty_guard(wizard_env, monkeypatch):
    monkeypatch.setattr(loadout_wizard, "_stdin_is_tty", lambda: False)
    result = runner.invoke(app, ["loadout", "create", "pack", "--from-project"])
    assert result.exit_code == 1
    assert "interactive terminal" in result.output


def test_wizard_flags_require_from_project(wizard_env):
    for flags in (["--harness", "claude-code"], ["--manifest-out", "m.json"]):
        result = runner.invoke(app, ["loadout", "create", "pack", *flags])
        assert result.exit_code == 1
        assert "--from-project" in result.output


def test_plain_create_still_works(wizard_env):
    calls = wizard_env
    result = runner.invoke(app, ["loadout", "create", "plain-pack"])
    assert result.exit_code == 0
    assert calls[0]["json_body"]["loadout"]["name"] == "Plain Pack"
