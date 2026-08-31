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


def _row(name, scope="project", harnesses=("claude-code",), selected=False,
        source="friend/x@v1"):
    # Fabricates a _Row directly, bypassing _build_rows, for unit-testing
    # _row_label/_label_width without a scan.
    return loadout_wizard._Row(
        contributor=contributor(name, scope=scope, harnesses=harnesses, source=source),
        harnesses=list(harnesses), scope=scope, selected=selected,
    )


def _accept_preselected(rows, chosen_harness=None):
    # Stands in for a user who accepts questionary's pre-checked rows as-is.
    return [r for r in rows if r.selected]


def _accept_all(rows, chosen_harness=None):
    # Stands in for a user who checks every row, including unselected ones.
    return list(rows)


def _pick_all_harnesses(harness_ids):
    # Stands in for a user who picks "All harnesses" in the harness picker.
    return None


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
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)

    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")  # confirm
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
    monkeypatch.setattr(
        loadout_wizard, "_choose_skills",
        lambda rows, chosen: [r for r in rows if r.contributor.name == "alpha"],
    )

    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert [entry["name"] for entry in entries] == ["alpha"]


def test_sections_and_preselection(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(
        contributor("proj-skill", scope="project"),
        contributor("user-skill", scope="user"),
    ))
    captured = {}

    def fake_choose_skills(rows, chosen):
        captured["rows"] = rows
        return []

    monkeypatch.setattr(loadout_wizard, "_choose_skills", fake_choose_skills)
    runner.invoke(app, ["loadout", "create", "pack"])
    rows = captured["rows"]
    # Project-scope rows sort before user-scope rows and are the only ones
    # pre-checked; this is what a real questionary checkbox would receive as
    # its `checked=` state per row.
    assert [row.scope for row in rows] == ["project", "user"]
    proj_row = next(r for r in rows if r.contributor.name == "proj-skill")
    user_row = next(r for r in rows if r.contributor.name == "user-skill")
    assert proj_row.selected is True
    assert user_row.selected is False


def test_user_scope_is_unselected_by_default(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("proj-skill", scope="project"),
        contributor("user-skill", scope="user"),
    ))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
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
    captured = {}

    def fake_choose_skills(rows, chosen):
        captured["rows"] = rows
        captured["chosen"] = chosen
        return list(rows)

    monkeypatch.setattr(loadout_wizard, "_choose_skills", fake_choose_skills)
    result = runner.invoke(
        app, ["loadout", "create", "pack", "--harness", "claude-code"], input="y\n",
    )
    assert result.exit_code == 0, result.output
    names = {row.contributor.name for row in captured["rows"]}
    assert names == {"cc-only", "both"}  # pi-only filtered out before the picker
    assert captured["chosen"] == "claude-code"
    # a harness was chosen (via --harness), so labels carry no badge even
    # though "both" spans two harnesses
    both_row = next(r for r in captured["rows"] if r.contributor.name == "both")
    assert "[" not in loadout_wizard._row_label(both_row, captured["chosen"])
    published = {entry["name"] for entry in calls[1]["json_body"]["manifest"]["entries"]}
    assert published == {"cc-only", "both"}


def test_system_contributors_are_skipped(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(contributor("vendored", system=True)))
    result = runner.invoke(app, ["loadout", "create", "pack"], input="")
    assert result.exit_code == 1
    assert "No skills found" in result.output


def test_local_only_warning_in_summary(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(contributor("untracked", prov_kind="unmanaged", source=None)))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert "local-only" in result.output
    assert "blocks making this loadout public" in result.output


def test_decline_at_confirm_makes_no_server_calls(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha")))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="n\n")
    assert result.exit_code == 0
    assert calls == []


def test_zero_selection_exits(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha")))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", lambda rows, chosen: [])
    result = runner.invoke(app, ["loadout", "create", "pack"])
    assert result.exit_code == 1
    assert "Nothing selected" in result.output
    assert calls == []


def test_publish_failure_reports_created_but_empty(wizard_env, monkeypatch, tmp_path):
    set_world(monkeypatch, make_world(contributor("alpha")))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)

    def failing_api(method, path, token=None, json_body=None, base_url=None, raw=False):
        if path == "/api/v1/loadouts":
            return {"loadout": {"owner": "drew", "slug": "pack", "name": "Pack",
                                "visibility": "private", "description": None,
                                "published_at": None, "current_revision": None}}
        raise service.ServiceError("revision_invalid", "The revision manifest is invalid.",
                                   details={"manifest": ["boom"]})

    monkeypatch.setattr(service, "api_request", failing_api)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 1
    assert "Created drew/pack, but the publish failed" in result.output
    assert "drskill loadout publish drew/pack" in result.output
    saved = [token for token in result.output.split() if token.endswith(".json")]
    assert saved, result.output
    manifest = json.loads(Path(saved[-1]).read_text(encoding="utf-8"))
    assert manifest["entries"][0]["name"] == "alpha"


def test_manifest_out_writes_the_manifest(wizard_env, monkeypatch, tmp_path):
    set_world(monkeypatch, make_world(contributor("alpha")))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    out = tmp_path / "m.json"
    result = runner.invoke(
        app, ["loadout", "create", "pack", "--manifest-out", str(out)], input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 1


def test_manifest_out_not_written_on_decline(wizard_env, monkeypatch, tmp_path):
    calls = wizard_env
    set_world(monkeypatch, make_world(contributor("alpha")))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    out = tmp_path / "m.json"
    result = runner.invoke(
        app, ["loadout", "create", "pack", "--manifest-out", str(out)], input="n\n",
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
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_all)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    names = {entry["name"] for entry in calls[1]["json_body"]["manifest"]["entries"]}
    assert names == {"proj-skill", "user-skill"}


def test_create_failure_stops_before_publish(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(contributor("alpha")))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    calls = []

    def failing_create(method, path, token=None, json_body=None, base_url=None, raw=False):
        calls.append(path)
        raise service.ServiceError(
            "loadout_invalid", "The loadout is invalid.", details={"slug": ["is invalid"]}
        )

    monkeypatch.setattr(service, "api_request", failing_create)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
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
    monkeypatch.setattr(loadout_wizard, "_choose_harness", _pick_all_harnesses)
    captured = {}

    def fake_choose_skills(rows, chosen):
        captured["rows"] = rows
        captured["chosen"] = chosen
        return [r for r in rows if r.selected]

    monkeypatch.setattr(loadout_wizard, "_choose_skills", fake_choose_skills)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    row = captured["rows"][0]
    assert "[claude-code, codex]" in loadout_wizard._row_label(row, captured["chosen"])
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
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert len(entries) == 1


def test_dedup_local_only_different_hash_merges_deterministically(wizard_env, monkeypatch):
    # Rows merge on (kind, normalized name) alone, so two local-only copies
    # of the same name merge even with different content hashes. Neither
    # member is tracked, so the representative is picked by id
    # (lexicographically first) for determinism.
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("untracked", id="/tmp/untracked-a", prov_kind="unmanaged", source=None,
                    content_hash="sha256:" + "11" * 32),
        contributor("untracked", id="/tmp/untracked-b", prov_kind="unmanaged", source=None,
                    content_hash="sha256:" + "22" * 32),
    ))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert len(entries) == 1
    assert entries[0]["content_hash"] == "sha256:" + "11" * 32


def test_dedup_merges_local_and_tracked_copies(wizard_env, monkeypatch):
    # A local-only copy and a plugin-tracked copy of the same skill merge
    # into one row; the tracked member wins as representative so the
    # published entry carries real provenance instead of local_only.
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("alpha", id="/tmp/alpha-local", prov_kind="unmanaged", source=None,
                    content_hash="sha256:" + "33" * 32),
        contributor("alpha", id="/tmp/alpha-tracked", prov_kind="gh-skill",
                    source="friend/x@v1", content_hash="sha256:" + "44" * 32),
    ))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "friend/x@v1" in result.output  # source summary shows the tracked source
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert len(entries) == 1
    assert entries[0]["local_only"] is False
    assert entries[0]["source_type"] == "github"


def test_dedup_merges_scope_prefers_project(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("alpha", id="/tmp/alpha-user", scope="user", harnesses=("claude-code",)),
        contributor("alpha", id="/tmp/alpha-project", scope="project", harnesses=("codex",)),
    ))
    monkeypatch.setattr(loadout_wizard, "_choose_harness", _pick_all_harnesses)
    captured = {}

    def fake_choose_skills(rows, chosen):
        captured["rows"] = rows
        return [r for r in rows if r.selected]

    monkeypatch.setattr(loadout_wizard, "_choose_skills", fake_choose_skills)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    assert [row.scope for row in captured["rows"]] == ["project"]  # the one merged row
    assert captured["rows"][0].selected is True
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert len(entries) == 1


def test_harness_step_prompts_when_multiple_harnesses(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("cc-skill", harnesses=("claude-code",)),
        contributor("codex-skill", harnesses=("codex",)),
    ))
    picked = {}

    def fake_choose_harness(harness_ids):
        picked["ids"] = harness_ids
        return "claude-code"

    monkeypatch.setattr(loadout_wizard, "_choose_harness", fake_choose_harness)
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    assert picked["ids"] == ["claude-code", "codex"]
    entries = calls[1]["json_body"]["manifest"]["entries"]
    assert [entry["name"] for entry in entries] == ["cc-skill"]


def test_harness_step_all_keeps_everything(wizard_env, monkeypatch):
    calls = wizard_env
    set_world(monkeypatch, make_world(
        contributor("cc-skill", harnesses=("claude-code",)),
        contributor("codex-skill", harnesses=("codex",)),
    ))
    monkeypatch.setattr(loadout_wizard, "_choose_harness", _pick_all_harnesses)
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    names = {entry["name"] for entry in calls[1]["json_body"]["manifest"]["entries"]}
    assert names == {"cc-skill", "codex-skill"}


def test_harness_step_absent_for_single_harness(wizard_env, monkeypatch):
    set_world(monkeypatch, make_world(contributor("alpha", harnesses=("claude-code",))))

    def boom(harness_ids):
        raise AssertionError("should not be called for a single-harness world")

    monkeypatch.setattr(loadout_wizard, "_choose_harness", boom)
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output


def test_scan_status_spinner_does_not_crash(wizard_env, monkeypatch):
    # The scan step drives a rich console.status spinner instead of a static
    # line; this exercises that path end to end under CliRunner's captured,
    # non-tty stdout.
    set_world(monkeypatch, make_world(contributor("alpha")))
    monkeypatch.setattr(loadout_wizard, "_choose_skills", _accept_preselected)
    result = runner.invoke(app, ["loadout", "create", "pack"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Published revision 1" in result.output


def test_row_label_pads_short_names_to_the_given_width():
    row = _row("x")
    label = loadout_wizard._row_label(row, "claude-code", width=24)
    assert label.startswith("x" + " " * 23 + "  ")


def test_row_label_does_not_truncate_names_longer_than_the_width():
    long_name = "a" * 40
    row = _row(long_name)
    label = loadout_wizard._row_label(row, "claude-code", width=34)
    assert label.startswith(long_name + "  ")


def test_row_label_strips_version_suffix():
    row = _row("alpha", source="superpowers@claude-plugins-official==6.3.0")
    label = loadout_wizard._row_label(row, "claude-code")
    assert "superpowers@claude-plugins-official" in label
    assert "==6.3.0" not in label


def test_row_label_keeps_sources_without_a_version_suffix():
    row = _row("alpha", source="friend/x@v1")
    label = loadout_wizard._row_label(row, "claude-code")
    assert "friend/x@v1" in label


def test_row_label_no_badge_when_a_harness_is_chosen():
    row = _row("alpha", harnesses=("claude-code", "codex"))
    label = loadout_wizard._row_label(row, "claude-code")
    assert "[" not in label


def test_row_label_lists_one_or_two_harnesses_when_none_chosen():
    row1 = _row("alpha", harnesses=("claude-code",))
    row2 = _row("beta", harnesses=("claude-code", "codex"))
    assert "[claude-code]" in loadout_wizard._row_label(row1, None)
    assert "[claude-code, codex]" in loadout_wizard._row_label(row2, None)


def test_row_label_compact_badge_for_more_than_two_harnesses():
    row = _row("alpha", harnesses=("claude-code", "codex", "pi", "gemini"))
    label = loadout_wizard._row_label(row, None)
    assert "[claude-code +3]" in label


def test_label_width_floors_at_24_and_caps_at_34():
    short_rows = [_row("a"), _row("bb")]
    assert loadout_wizard._label_width(short_rows) == 24
    long_rows = [_row("a" * 40)]
    assert loadout_wizard._label_width(long_rows) == 34
    mid_rows = [_row("a" * 28)]
    assert loadout_wizard._label_width(mid_rows) == 28
    assert loadout_wizard._label_width([]) == 24


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
