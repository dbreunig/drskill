# Loadout Create Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `drskill loadout create <slug> --from-project` scans the project, lets the user toggle a harness-agnostic selection of active skills (project and user scope shown together as sections), and publishes the confirmed selection as revision 1 with two-step failure reporting.

**Architecture:** A pure mapping module `manifest_build.py` turns scanner `Contributor`s into a manifest dict plus human-readable notes (normalization, dedup, local-only). A new `loadout_wizard.py` orchestrates: credentials check → `pipeline.run_scan` → sectioned selection loop (plain typer prompts, no new deps) → summary + confirm → create then publish via the existing service client, saving the manifest on failure. `cli.py`'s `create` command gains `--from-project`, `--harness`, `--manifest-out` and delegates.

**Tech Stack:** Python 3.11+/Typer/rich (existing deps only), pytest via `uv run pytest`.

**Spec:** `docs/superpowers/specs/2026-08-31-loadout-wizard-design.md`

## Global Constraints

- Repo: `/Users/dbreunig/Development/drskill`, branch `loadout-wizard` (create in Task 1: `git checkout -b loadout-wizard`). Baseline: 774 passed, 11 pre-existing warnings.
- TDD; focused test file while iterating; full `uv run pytest -q` plus `uv run ruff check src/drskill` before every commit.
- No new dependencies; the selection UI is print + prompt loops.
- All service calls through module attributes (`service.api_request`, `pipeline.run_scan`) so tests can monkeypatch; never `from drskill.service import X` style imports of individual functions.
- Existing interfaces reused (do not rewrite): `Contributor` (fields: `kind` in {"skill","mcp_tool"}, `name`, `scope` in {"project","user"}, `deployments[].harness`, `source: Provenance(kind, source)`, `content_hash` already `sha256:`-prefixed, `system: bool`); `World.contributors: dict[str, Contributor]`; `pipeline.run_scan(project_root, home, global_only=False, config=None, harness=None, ..., progress=None) -> (World, findings)`; `service.canonical_manifest`, `service.api_request`, `ServiceError(.code/.message/.details)`; cli helpers `_service_credentials()`, `_parse_ref`, `_home()`, `_validate_harness(harness)`, module `console`; server selector rule `[a-z0-9][a-z0-9._-]*`.
- Manifest envelope produced by the wizard: `{"schema_version": 1, "reproducible": False, "entries": [...], "harness_mappings": []}`.
- Error UX matches the loadout commands: messages via `typer.echo`, exit 1; not-signed-in hint comes from `_service_credentials`.

---

### Task 1: manifest_build — contributors to manifest

**Files:**
- Create: `src/drskill/manifest_build.py`
- Test: `tests/test_manifest_build.py`

**Interfaces:**
- Produces: `normalize_name(name: str) -> str`; `contributors_to_manifest(contributors: list[Contributor]) -> tuple[dict, list[str]]` returning the manifest dict and human-readable notes (name normalizations, selector dedups). Task 2 calls both and shows the notes in the summary.

- [ ] **Step 1: Create the branch and write the failing tests**

```bash
git checkout -b loadout-wizard
```

Create `tests/test_manifest_build.py`:

```python
from drskill import manifest_build
from drskill.models import Contributor, Provenance, TokenCost


def contributor(name, kind="skill", prov_kind="gh-skill", source="friend/skill@v1",
                scope="project", content_hash="sha256:" + "ab" * 32):
    return Contributor(
        id=f"/tmp/{name}",
        kind=kind,
        name=name,
        source=Provenance(kind=prov_kind, source=source),
        scope=scope,
        token_cost=TokenCost(catalog_tokens=1, body_tokens=1),
        content_hash=content_hash,
    )


def test_envelope_shape_and_github_mapping():
    manifest, notes = manifest_build.contributors_to_manifest([contributor("citation-style")])
    assert manifest["schema_version"] == 1
    assert manifest["reproducible"] is False
    assert manifest["harness_mappings"] == []
    entry = manifest["entries"][0]
    assert entry == {
        "kind": "skill",
        "selector": "skill:citation-style",
        "name": "citation-style",
        "source_type": "github",
        "source_reference": "friend/skill@v1",
        "content_hash": "sha256:" + "ab" * 32,
        "local_only": False,
        "metadata": {},
    }
    assert notes == []


def test_provenance_kinds_map_to_source_types():
    cases = {
        "gh-skill": ("github", False),
        "skills-lock": ("github", False),
        "plugin": ("plugin", False),
        "linked": ("local", True),
        "unmanaged": ("local", True),
    }
    for prov_kind, (source_type, local_only) in cases.items():
        manifest, _ = manifest_build.contributors_to_manifest(
            [contributor("x", prov_kind=prov_kind, source="somewhere")]
        )
        entry = manifest["entries"][0]
        assert entry["source_type"] == source_type, prov_kind
        assert entry["local_only"] is local_only, prov_kind


def test_missing_source_forces_local_only():
    manifest, _ = manifest_build.contributors_to_manifest(
        [contributor("x", prov_kind="gh-skill", source=None)]
    )
    entry = manifest["entries"][0]
    assert entry["local_only"] is True
    assert entry["source_type"] == "local"
    assert entry["source_reference"] == "/tmp/x"


def test_mcp_tool_kind():
    manifest, _ = manifest_build.contributors_to_manifest([contributor("papers", kind="mcp_tool")])
    assert manifest["entries"][0]["kind"] == "mcp"
    assert manifest["entries"][0]["selector"] == "mcp:papers"


def test_name_normalization_with_note():
    manifest, notes = manifest_build.contributors_to_manifest([contributor("My Skill!")])
    assert manifest["entries"][0]["name"] == "my-skill"
    assert manifest["entries"][0]["selector"] == "skill:my-skill"
    assert any("My Skill!" in note and "my-skill" in note for note in notes)


def test_duplicate_selectors_get_suffixes_with_note():
    manifest, notes = manifest_build.contributors_to_manifest(
        [contributor("dup"), contributor("dup"), contributor("dup")]
    )
    selectors = [entry["selector"] for entry in manifest["entries"]]
    assert selectors == ["skill:dup", "skill:dup-2", "skill:dup-3"]
    assert any("dup-2" in note for note in notes)


def test_normalize_name_rules():
    assert manifest_build.normalize_name("Citation Style") == "citation-style"
    assert manifest_build.normalize_name("__weird--Name__") == "weird-name"
    assert manifest_build.normalize_name("ok.name_1") == "ok.name_1"
    assert manifest_build.normalize_name("!!!") == "skill"


def test_manifest_is_canonicalizable():
    from drskill import service

    manifest, _ = manifest_build.contributors_to_manifest([contributor("a"), contributor("b")])
    canonical, runtime_hash = service.canonical_manifest(manifest)
    assert runtime_hash.startswith("sha256:")
    assert '"entries"' in canonical
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_manifest_build.py -q`
Expected: FAIL (no module).

- [ ] **Step 3: Implement**

Create `src/drskill/manifest_build.py`:

```python
"""Turn scanner Contributors into a publishable loadout manifest.

Pure functions, no UI and no network, so the mapping is testable on its own.
The server's selector rule is [a-z0-9][a-z0-9._-]* after the kind prefix.
"""

from __future__ import annotations

import re

from drskill.models import Contributor

_INVALID = re.compile(r"[^a-z0-9._-]+")
_COLLAPSE = re.compile(r"-{2,}")

_SOURCE_TYPES = {
    "gh-skill": "github",
    "skills-lock": "github",
    "plugin": "plugin",
}

_KINDS = {"skill": "skill", "mcp_tool": "mcp"}


def normalize_name(name: str) -> str:
    lowered = name.strip().lower()
    replaced = _INVALID.sub("-", lowered)
    collapsed = _COLLAPSE.sub("-", replaced).strip("-_.")
    return collapsed or "skill"


def contributors_to_manifest(contributors: list[Contributor]) -> tuple[dict, list[str]]:
    entries: list[dict] = []
    notes: list[str] = []
    used_selectors: set[str] = set()

    for contributor in contributors:
        kind = _KINDS[contributor.kind]
        name = normalize_name(contributor.name)
        if name != contributor.name:
            notes.append(f"renamed {contributor.name!r} to {name!r} to fit the selector rules")

        selector = f"{kind}:{name}"
        if selector in used_selectors:
            suffix = 2
            while f"{selector}-{suffix}" in used_selectors:
                suffix += 1
            selector = f"{selector}-{suffix}"
            name = f"{name}-{suffix}"
            notes.append(f"renamed a duplicate of {contributor.name!r} to {name!r}")
        used_selectors.add(selector)

        source_type = _SOURCE_TYPES.get(contributor.source.kind)
        source = contributor.source.source
        local_only = source_type is None or not source
        entries.append(
            {
                "kind": kind,
                "selector": selector,
                "name": name,
                "source_type": "local" if local_only else source_type,
                "source_reference": source or contributor.id,
                "content_hash": contributor.content_hash,
                "local_only": local_only,
                "metadata": {},
            }
        )

    return (
        {
            "schema_version": 1,
            "reproducible": False,
            "entries": entries,
            "harness_mappings": [],
        },
        notes,
    )
```

(Note the `source_reference` expression: when `local_only`, prefer the provenance source if present, else the contributor id path; when not local, it is the provenance source. Simplify to a plain if/else block if the inline form reads poorly — behavior per the tests is what is binding.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_manifest_build.py -q`, then `uv run pytest -q`, then `uv run ruff check src/drskill`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/manifest_build.py tests/test_manifest_build.py
git commit -m "feat: map scanner contributors to a loadout manifest"
```

---

### Task 2: the wizard module and CLI wiring

**Files:**
- Create: `src/drskill/loadout_wizard.py`
- Modify: `src/drskill/cli.py` (create command flags + delegation)
- Test: `tests/test_loadout_wizard.py`

**Interfaces:**
- Consumes: Task 1's `manifest_build`, `pipeline.run_scan`, `service.*`, cli helpers.
- Produces: `loadout_wizard.run(slug, name, description, harness, manifest_out, creds, base_url, home) -> None` (raises `typer.Exit`); `loadout_wizard._stdin_is_tty()` (monkeypatch seam); `create` flags `--from-project`, `--harness`, `--manifest-out`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_loadout_wizard.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_loadout_wizard.py -q`
Expected: FAIL (no module / unknown flags).

- [ ] **Step 3: Implement the wizard module**

Create `src/drskill/loadout_wizard.py`:

```python
"""Interactive create flow: scan the project, pick skills, create + publish.

Selection happens over harness-agnostic contributors (the scanner already
collapses per-harness sightings); harness names appear only as badges and as
an optional filter. See docs/superpowers/specs/2026-08-31-loadout-wizard-design.md.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import typer

from drskill import manifest_build, pipeline, service
from drskill.models import Contributor


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


@dataclass
class _Row:
    contributor: Contributor
    selected: bool


def run(
    slug: str,
    name: str,
    description: str | None,
    harness: str | None,
    manifest_out: Path | None,
    creds: dict,
    base_url: str,
    home: Path,
) -> None:
    if not _stdin_is_tty():
        typer.echo("--from-project needs an interactive terminal.")
        raise typer.Exit(1)

    typer.echo("Scanning the current project...")
    # Scan ALL harnesses even when --harness filters the list: the filter
    # narrows which rows appear, but badges must still show every harness a
    # skill is deployed to (a harness-scoped scan would lose that).
    world, _findings = pipeline.run_scan(Path.cwd(), home)

    rows = _build_rows(world, harness)
    if not rows:
        typer.echo("No skills found in this project to include.")
        raise typer.Exit(1)

    _selection_loop(rows)

    selected = [row.contributor for row in rows if row.selected]
    if not selected:
        typer.echo("Nothing selected.")
        raise typer.Exit(1)

    manifest, notes = manifest_build.contributors_to_manifest(selected)
    _print_summary(manifest, notes)
    if manifest_out:
        manifest_out.write_bytes(json.dumps(manifest, indent=2).encode())
        typer.echo(f"Wrote manifest to {manifest_out}")

    if not typer.confirm(
        f"Create '{slug}' and publish these "
        f"{len(manifest['entries'])} entries as revision 1?", default=False
    ):
        raise typer.Exit(0)

    ref = _create_loadout(slug, name, description, creds, base_url)
    _publish(ref, manifest, manifest_out, creds, base_url)


def _build_rows(world, harness: str | None) -> list[_Row]:
    contributors = [
        c for c in world.contributors.values()
        if not c.system
        and (harness is None or any(d.harness == harness for d in c.deployments))
    ]
    contributors.sort(key=lambda c: (c.scope != "project", c.name.lower()))
    return [_Row(contributor=c, selected=(c.scope == "project")) for c in contributors]


def _render(rows: list[_Row]) -> None:
    current_scope = None
    for index, row in enumerate(rows, start=1):
        if row.contributor.scope != current_scope:
            current_scope = row.contributor.scope
            typer.echo(f"\n{'Project scope' if current_scope == 'project' else 'User scope'}")
        harnesses = sorted({d.harness for d in row.contributor.deployments})
        badge = f"  [{', '.join(harnesses)}]" if harnesses else ""
        source = row.contributor.source.source or "local only"
        mark = "x" if row.selected else " "
        typer.echo(f"  [{mark}] {index:>2}  {row.contributor.name}  ({source}){badge}")
    typer.echo("")


def _selection_loop(rows: list[_Row]) -> None:
    while True:
        _render(rows)
        raw = typer.prompt(
            "Toggle numbers (space separated), 'a' all, 'n' none, enter to accept",
            default="", show_default=False,
        ).strip().lower()
        if raw == "":
            return
        if raw == "a":
            for row in rows:
                row.selected = True
            continue
        if raw == "n":
            for row in rows:
                row.selected = False
            continue
        for token in raw.replace(",", " ").split():
            if token.isdigit() and 1 <= int(token) <= len(rows):
                row = rows[int(token) - 1]
                row.selected = not row.selected


def _print_summary(manifest: dict, notes: list[str]) -> None:
    entries = manifest["entries"]
    local_only = [entry for entry in entries if entry["local_only"]]
    typer.echo(f"Selected {len(entries)} entries:")
    for entry in entries:
        marker = "  local-only" if entry["local_only"] else f"  {entry['source_reference']}"
        typer.echo(f"  {entry['selector']}{marker}")
    for note in notes:
        typer.echo(f"  note: {note}")
    if local_only:
        typer.echo(
            f"{len(local_only)} skills have no tracked source and will be marked "
            "local-only. That is fine for a private loadout, but it blocks making "
            "this loadout public."
        )


def _create_loadout(slug, name, description, creds, base_url) -> str:
    body: dict = {"loadout": {"slug": slug, "name": name}}
    if description is not None:
        body["loadout"]["description"] = description
    try:
        data = service.api_request(
            "POST", "/api/v1/loadouts", token=creds["token"], json_body=body, base_url=base_url
        )
    except service.ServiceError as err:
        typer.echo(err.message)
        for field, messages in (err.details or {}).items():
            for message in messages if isinstance(messages, list) else [messages]:
                typer.echo(f"  {field}: {message}")
        raise typer.Exit(1)
    loadout = data["loadout"]
    return f"{loadout['owner']}/{loadout['slug']}"


def _publish(ref, manifest, manifest_out, creds, base_url) -> None:
    _, runtime_hash = service.canonical_manifest(manifest)
    try:
        data = service.api_request(
            "POST", f"/api/v1/loadouts/{ref}/revisions",
            token=creds["token"],
            json_body={"manifest": manifest, "runtime_hash": runtime_hash},
            base_url=base_url,
        )
    except service.ServiceError as err:
        saved = manifest_out
        if saved is None:
            fd, temp_name = tempfile.mkstemp(prefix="drskill-manifest-", suffix=".json")
            saved = Path(temp_name)
            with open(fd, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, indent=2)
        typer.echo(f"Created {ref}, but the publish failed:")
        typer.echo(f"  {err.message}")
        for field, messages in (err.details or {}).items():
            for message in messages if isinstance(messages, list) else [messages]:
                typer.echo(f"  {field}: {message}")
        typer.echo("The loadout exists and is empty. Fix the manifest and run:")
        typer.echo(f"  drskill loadout publish {ref} {saved}")
        raise typer.Exit(1)
    revision = data["revision"]
    typer.echo(f"Created {ref}")
    typer.echo(f"Published revision {revision['number']} ({revision['runtime_hash']})")
```

Remove the `creds_handle_hint` indirection if it reads as noise: the confirm line may simply say `Create '<slug>' and publish these N entries as revision 1?` — the handle is unknown until the server responds, and the tests only assert the confirm happens. Keep whichever reads plainer; do not invent a handle.

- [ ] **Step 4: Wire the create command**

In `src/drskill/cli.py`, extend `create`:

```python
@loadout_app.command()
def create(
    slug: str = typer.Argument(..., help="URL slug for the new loadout"),
    name: str | None = typer.Option(None, "--name", help="display name (defaults from the slug)"),
    description: str | None = typer.Option(None, "--description", help="optional description"),
    from_project: bool = typer.Option(False, "--from-project",
        help="pick the contents interactively from this project's active skills"),
    harness: str | None = typer.Option(None, "--harness",
        help="with --from-project: only list skills active in this harness"),
    manifest_out: Path | None = typer.Option(None, "--manifest-out",
        help="with --from-project: also save the generated manifest to a file"),
) -> None:
    """Create a private loadout on the drskill service."""
    if not from_project and (harness is not None or manifest_out is not None):
        typer.echo("--harness and --manifest-out require --from-project.")
        raise typer.Exit(1)
    creds, base = _service_credentials()
    if name is None:
        name = slug.replace("-", " ").title()
    if from_project:
        if harness is not None:
            _validate_harness(harness)
        from drskill import loadout_wizard

        loadout_wizard.run(slug, name, description, harness, manifest_out,
                           creds, base, _home())
        return
    # existing non-interactive body unchanged from here down
```

(Keep the existing non-interactive create body exactly as it is today, including the post-create status block. The wizard path returns before it.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_loadout_wizard.py tests/test_cli_loadouts.py -q`, then `uv run pytest -q`, then `uv run ruff check src/drskill`.
Expected: PASS. Adjust the two rendering assertions in `test_sections_and_preselection` to the actual row format if the exact spacing differs — the binding intent is: sections in order, project rows preselected, user rows not.

- [ ] **Step 6: Commit**

```bash
git add src/drskill tests/test_loadout_wizard.py
git commit -m "feat: add the --from-project wizard to loadout create"
```

---

### Task 3: Verification and wrap-up

- [ ] **Step 1: Full suite + lint**

Run: `uv run pytest -q` and `uv run ruff check src/drskill` — all green.

- [ ] **Step 2: Manual smoke** (needs a real terminal, so this step is for the human: run `drskill loadout create wizard-test --from-project` inside a project with active skills, against the dev server, and walk the toggle/confirm flow once).

- [ ] **Step 3: Finish**

Use superpowers:finishing-a-development-branch (branch `loadout-wizard`).
