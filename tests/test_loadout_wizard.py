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
                harnesses=("claude-code",), system=False, content_hash=None, id=None):
    return Contributor(
        id=id or f"/tmp/{name}",
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
        content_hash=content_hash or "sha256:" + "ab" * 32,
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

    result = runner.invoke(app, ["loadout", "create", "pack"],
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

    result = runner.invoke(app, ["loadout", "create", "pack"],
                           input="2\n\ny\n")  # toggle #2 off, accept, confirm
    assert result.exit_code == 0, result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert [entry["name"] for entry in entries] == ["alpha"]


def test_sections_and_preselection(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(
        contributor("proj-skill", scope="project"),
        contributor("user-skill", scope="user"),
    ))
    result = runner.invoke(app, ["loadout", "create", "pack"],
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
    result = runner.invoke(app, ["loadout", "create", "pack"],
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
        app, ["loadout", "create", "pack", "--harness", "claude-code"],
        input="\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "pi-only" not in result.output
    assert "[claude-code, pi]" in result.output
    names = {entry["name"] for entry in calls[1]["json_body"]["manifest"]["entries"]}
    assert names == {"cc-only", "both"}


def test_system_contributors_are_skipped(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(contributor("vendored", system=True)))
    result = runner.invoke(app, ["loadout", "create", "pack"], input="")
    assert result.exit_code == 1
    assert "No skills found" in result.output


def test_local_only_warning_in_summary(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(contributor("untracked", prov_kind="unmanaged", source=None)))
    result = runner.invoke(app, ["loadout", "create", "pack"],
                           input="\ny\n")
    assert "local-only" in result.output
    assert "blocks making this loadout public" in result.output


def test_decline_at_confirm_makes_no_server_calls(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha")))
    result = runner.invoke(app, ["loadout", "create", "pack"],
                           input="\nn\n")
    assert result.exit_code == 0
    assert calls == []


def test_zero_selection_exits(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha")))
    result = runner.invoke(app, ["loadout", "create", "pack"],
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
    result = runner.invoke(app, ["loadout", "create", "pack"],
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
        app, ["loadout", "create", "pack", "--manifest-out", str(out)],
        input="\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 1


def test_manifest_out_not_written_on_decline(wizard_env, monkeypatch, tmp_path):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha")))
    out = tmp_path / "m.json"
    result = runner.invoke(
        app, ["loadout", "create", "pack", "--manifest-out", str(out)],
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
    result = runner.invoke(app, ["loadout", "create", "pack"],
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
    result = runner.invoke(app, ["loadout", "create", "pack"],
                           input="\ny\n")
    assert result.exit_code == 1
    assert "The loadout is invalid." in result.output
    assert "is invalid" in result.output
    assert calls == ["/api/v1/loadouts"]


def test_unknown_harness_through_wizard(wizard_env):
    result = runner.invoke(
        app, ["loadout", "create", "pack", "--harness", "nope"],
    )
    assert result.exit_code == 1
    assert "unknown harness" in result.output
    assert "valid ids" in result.output


def test_dedup_tracked_across_harnesses(wizard_env, monkeypatch):
    # Same tracked skill materialized once per harness (e.g. via a plugin
    # install): same name + same provenance source, distinct ids/paths.
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("alpha", id="/tmp/alpha-cc", harnesses=("claude-code",)),
        contributor("alpha", id="/tmp/alpha-codex", harnesses=("codex",)),
    ))
    result = runner.invoke(app, ["loadout", "create", "pack"],
                           input="a\n\ny\n")  # keep all harnesses, accept, confirm
    assert result.exit_code == 0, result.output
    assert "[claude-code, codex]" in result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert len(entries) == 1
    assert entries[0]["name"] == "alpha"


def test_dedup_local_only_same_hash_merges(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("untracked", id="/tmp/untracked-a", prov_kind="unmanaged", source=None,
                    content_hash="sha256:" + "11" * 32),
        contributor("untracked", id="/tmp/untracked-b", prov_kind="unmanaged", source=None,
                    content_hash="sha256:" + "11" * 32),
    ))
    result = runner.invoke(app, ["loadout", "create", "pack"], input="\ny\n")
    assert result.exit_code == 0, result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert len(entries) == 1


def test_dedup_local_only_different_hash_keeps_separate_rows(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("untracked", id="/tmp/untracked-a", prov_kind="unmanaged", source=None,
                    content_hash="sha256:" + "11" * 32),
        contributor("untracked", id="/tmp/untracked-b", prov_kind="unmanaged", source=None,
                    content_hash="sha256:" + "22" * 32),
    ))
    result = runner.invoke(app, ["loadout", "create", "pack"], input="\ny\n")
    assert result.exit_code == 0, result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert len(entries) == 2


def test_dedup_merges_scope_prefers_project(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("alpha", id="/tmp/alpha-user", scope="user", harnesses=("claude-code",)),
        contributor("alpha", id="/tmp/alpha-project", scope="project", harnesses=("codex",)),
    ))
    result = runner.invoke(app, ["loadout", "create", "pack"],
                           input="a\n\ny\n")  # keep all harnesses, accept, confirm
    assert result.exit_code == 0, result.output
    assert "Project scope" in result.output
    assert "User scope" not in result.output  # the one merged row is project-scoped
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert len(entries) == 1


def test_harness_step_prompts_when_multiple_harnesses(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("cc-skill", harnesses=("claude-code",)),
        contributor("codex-skill", harnesses=("codex",)),
    ))
    result = runner.invoke(app, ["loadout", "create", "pack"],
                           input="1\n\ny\n")  # pick the sorted-first harness, accept, confirm
    assert result.exit_code == 0, result.output
    assert "Which harness's skills should this loadout draw from?" in result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert [entry["name"] for entry in entries] == ["cc-skill"]


def test_harness_step_all_keeps_everything(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("cc-skill", harnesses=("claude-code",)),
        contributor("codex-skill", harnesses=("codex",)),
    ))
    result = runner.invoke(app, ["loadout", "create", "pack"],
                           input="a\n\ny\n")
    assert result.exit_code == 0, result.output
    names = {entry["name"] for entry in calls[1]["json_body"]["manifest"]["entries"]}
    assert names == {"cc-skill", "codex-skill"}


def test_harness_step_absent_for_single_harness(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(contributor("alpha", harnesses=("claude-code",))))
    result = runner.invoke(app, ["loadout", "create", "pack"], input="\ny\n")
    assert result.exit_code == 0, result.output
    assert "Which harness's skills should this loadout draw from?" not in result.output


def test_scan_status_spinner_does_not_crash(wizard_env, monkeypatch):
    # The scan step drives a rich console.status spinner instead of a static
    # line; this exercises that path end to end under CliRunner's captured,
    # non-tty stdout.
    set_world(monkeypatch, make_world(contributor("alpha")))
    result = runner.invoke(app, ["loadout", "create", "pack"], input="\ny\n")
    assert result.exit_code == 0, result.output
    assert "Published revision 1" in result.output


def test_non_tty_falls_back_to_plain_create(wizard_env, monkeypatch):
    calls = wizard_env
    monkeypatch.setattr(loadout_wizard, "_stdin_is_tty", lambda: False)
    result = runner.invoke(app, ["loadout", "create", "pack"])
    assert result.exit_code == 0
    assert "contents: empty, no revisions yet" in result.output
    assert calls[0]["path"] == "/api/v1/loadouts"


def test_empty_flag_skips_the_wizard_interactively(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha")))
    result = runner.invoke(app, ["loadout", "create", "pack", "--empty"])
    assert result.exit_code == 0
    assert "contents: empty, no revisions yet" in result.output
    assert len(calls) == 1  # create only, no publish


def test_wizard_flags_error_when_wizard_cannot_run(wizard_env, monkeypatch):
    for blocker in ("non-tty", "empty"):
        if blocker == "non-tty":
            monkeypatch.setattr(loadout_wizard, "_stdin_is_tty", lambda: False)
            extra = []
        else:
            monkeypatch.setattr(loadout_wizard, "_stdin_is_tty", lambda: True)
            extra = ["--empty"]
        for flags in (["--harness", "claude-code"], ["--manifest-out", "m.json"]):
            result = runner.invoke(app, ["loadout", "create", "pack", *flags, *extra])
            assert result.exit_code == 1, (blocker, flags, result.output)
            assert "interactive" in result.output or "--empty" in result.output


def test_plain_create_still_works(wizard_env):
    calls = wizard_env
    result = runner.invoke(app, ["loadout", "create", "plain-pack", "--empty"])
    assert result.exit_code == 0
    assert calls[0]["json_body"]["loadout"]["name"] == "Plain Pack"
