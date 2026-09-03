# Loadout Status and Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `drskill loadout status` reports drift between local skills and published revisions; `drskill loadout update` republishes a loadout's entries from their local copies.

**Architecture:** A pure `loadout_drift.py` module matches revision entries to scanned contributors and classifies each as matches/changed/missing/unreadable/unchecked. The status command renders those classifications (plus optional upstream checks through `gh_source`); the update command feeds the changed set through the remediation review, refreshes hashes (re-uploading hosted content), and publishes revision N+1.

**Tech Stack:** drskill CLI only (pytest via `uv run pytest`). No server changes.

**Spec:** `docs/superpowers/specs/2026-09-02-loadout-status-update-design.md`

## Global Constraints

- Match by `manifest_build.normalize_name(contributor.name) == entry["name"]`, skill-kind contributors only; hash tiebreak among duplicates; note ambiguity.
- Comparison rules: drskill/github entries by `content.manifest_hash(content.collect_files(c))` (github legacy falls back to `resolution.content_hash`); local entries by `contributor.content_hash`.
- Status exit 1 when any changed/upstream-drift line printed, else 0. Update owner-only.
- Findings are displayed always; the a/s/q ack loop runs only interactively; never a gate.
- TDD; commits carry the session trailer from `git log -3`.

---

### Task 1: loadout_drift module

**Files:**
- Create: `src/drskill/loadout_drift.py`
- Test: `tests/test_loadout_drift.py`

**Interfaces:**
- Produces `EntryStatus` (dataclass: `entry: dict`, `contributor: Contributor | None`, `state: str` in {"matches","changed","missing","unreadable","unchecked"}, `note: str | None`) and `classify_entries(entries: list[dict], contributors: list[Contributor]) -> list[EntryStatus]`.

- [ ] **Step 1: Failing tests.** Reuse the contributor factory shape from `tests/test_manifest_build.py`. Cases: a hosted entry whose matched contributor's collected files hash to the entry hash → "matches"; different hash → "changed"; github entry with `directory_hash` likewise; legacy github entry (no directory_hash) compared via `resolution.content_hash` of the skill file text; local entry compared via `contributor.content_hash`; no matching contributor → "missing"; `collect_files` raising OSError → "unreadable"; mcp entry → "unchecked"; two same-named contributors where the second's hash matches → tiebreak picks it ("matches", no note); two same-named contributors, neither matching → first wins with an ambiguity note. Monkeypatch `content.collect_files` per case.

- [ ] **Step 2: Verify failure, implement.**

```python
"""Classify a revision's entries against locally scanned skills."""
from __future__ import annotations

from dataclasses import dataclass

from drskill import content, resolution
from drskill.manifest_build import normalize_name
from drskill.models import Contributor


@dataclass
class EntryStatus:
    entry: dict
    contributor: Contributor | None
    state: str  # matches | changed | missing | unreadable | unchecked
    note: str | None = None


def classify_entries(entries, contributors) -> list[EntryStatus]:
    skills = [c for c in contributors if c.kind == "skill"]
    by_name: dict[str, list[Contributor]] = {}
    for c in skills:
        by_name.setdefault(normalize_name(c.name), []).append(c)

    out = []
    for entry in entries:
        if entry.get("kind") != "skill":
            out.append(EntryStatus(entry, None, "unchecked"))
            continue
        candidates = by_name.get(entry.get("name"), [])
        if not candidates:
            out.append(EntryStatus(entry, None, "missing"))
            continue
        statuses = [_compare(entry, c) for c in candidates]
        for c, state in zip(candidates, statuses):
            if state == "matches":
                out.append(EntryStatus(entry, c, "matches"))
                break
        else:
            note = "several local skills share this name" if len(candidates) > 1 else None
            out.append(EntryStatus(entry, candidates[0], statuses[0], note))
    return out


def _compare(entry: dict, contributor: Contributor) -> str:
    source_type = entry.get("source_type")
    try:
        if source_type == "local":
            local = contributor.content_hash
            expected = entry.get("content_hash")
        elif source_type == "github" and not (entry.get("metadata") or {}).get("directory_hash"):
            local = contributor.content_hash
            expected = entry.get("content_hash")
        else:
            files = content.collect_files(contributor)
            local = content.manifest_hash(files)
            expected = (entry.get("metadata") or {}).get("directory_hash") \
                if source_type == "github" else entry.get("content_hash")
    except OSError:
        return "unreadable"
    return "matches" if local == expected else "changed"
```

Note: legacy github comparison uses `contributor.content_hash` (the scanner's normalized SKILL.md hash), which is what publish recorded — `resolution.content_hash` is only needed in tests to build expectations.

- [ ] **Step 3: Verify pass, commit** — `feat: classify local drift against revision entries`.

---

### Task 2: loadout status command

**Files:**
- Modify: `src/drskill/cli.py`
- Test: `tests/test_cli_status.py`

**Interfaces:**
- Consumes: `loadout_drift.classify_entries`, `pipeline.run_scan`, `gh_source` (for `--remote`), install's fake-API shape for tests.
- Produces: `drskill loadout status [ref] [--remote]`.

- [ ] **Step 1: Failing tests.** New file following `tests/test_cli_install.py`'s fixture style: fake `service.api_request` serving `GET /api/v1/loadouts` (list of one owned loadout with a current revision), the named show, and the revision manifest; monkeypatch `pipeline.run_scan` to return a built world (reuse the wizard tests' `make_world`/`contributor` helpers by importing or copying their builders). Cases: all-matching loadout exits 0 with `matches` lines; a changed hosted entry prints `changed locally since publish` plus the `loadout update` hint and exits 1; explicit-ref form hits the named endpoint and works on a non-owned viewable loadout without printing the update hint; a loadout without a revision is skipped with a note; `--remote` with the codeload stub prints `upstream has changed` for a drifted github entry (serve a tarball whose files differ) and exits 1, and stays quiet when upstream matches; mcp entry prints `not checked (mcp)`.

- [ ] **Step 2: Verify failure, implement.** In `cli.py`:

```python
@loadout_app.command()
def status(
    ref: str | None = typer.Argument(None, help="owner/slug (default: all your loadouts)"),
    remote: bool = typer.Option(False, "--remote", help="also fetch github entries' upstreams"),
) -> None:
    """Report drift between local skills and published loadout revisions."""
    from drskill import gh_source, loadout_drift

    creds, base = _service_credentials()
    home = _home()
    try:
        targets = _status_targets(ref, creds, base)
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    if not targets:
        typer.echo("No loadouts with a published revision.")
        return

    world, _ = _scan_with_status(
        lambda p: run_scan(Path.cwd(), home, False, ledger.load_effective_config(Path.cwd(), home, False), progress=p)
    )
    contributors = list(world.contributors.values())

    drifted = False
    for owner, slug, number, mine in targets:
        document = service.api_request(
            "GET", f"/api/v1/loadouts/{owner}/{slug}/revisions/{number}",
            token=creds["token"], base_url=base, raw=True)
        entries = json.loads(document).get("entries", [])
        typer.echo(f"\n{owner}/{slug} (revision {number})")
        changed_here = False
        for st in loadout_drift.classify_entries(entries, contributors):
            line = _STATUS_LINES[st.state]
            if remote and st.entry.get("source_type") == "github" and st.entry.get("kind") == "skill":
                line = _remote_line(st.entry, gh_source) or line
            typer.echo(f"  {st.entry['name']:<24} {line}"
                       + (f"  ({st.note})" if st.note else ""))
            if line in ("changed locally since publish", "upstream has changed"):
                changed_here = True
        if changed_here and mine:
            typer.echo(f"  Run drskill loadout update {owner}/{slug} to republish.")
        drifted = drifted or changed_here
    raise typer.Exit(1 if drifted else 0)
```

With `_STATUS_LINES = {"matches": "matches", "changed": "changed locally since publish", "missing": "not found on this machine", "unreadable": "unreadable", "unchecked": "not checked (mcp)"}`, `_status_targets` returning `(owner, slug, number, mine)` tuples (list endpoint for no-ref filtering out revisionless loadouts with a printed skip note; named endpoint plus identity-handle comparison for the ref form), and `_remote_line(entry, gh_source)` fetching coordinates/tarball/extract and returning `"upstream has changed"` on verify mismatch, `None` on match, or the FetchError message on failure. Check `_scan_with_status`'s actual signature in cli.py before wiring (scan/review already use it).

- [ ] **Step 3: Verify pass, full suite, commit** — `feat: add loadout status drift reporting`.

---

### Task 3: loadout update command

**Files:**
- Modify: `src/drskill/cli.py`
- Test: `tests/test_cli_update.py`

**Interfaces:**
- Consumes: `loadout_drift.classify_entries`, `content.upload/collect_files/manifest_hash`, `_review_fetched`, `service.canonical_manifest`, the fake-API shape.
- Produces: `drskill loadout update <ref> [--yes]`.

- [ ] **Step 1: Failing tests.** Fake API adds `GET /api/v1/identity`, records `POST .../revisions`, and records `POST /api/v1/content` (via monkeypatched `content.upload` returning `{"content_hash": <new>, "uploaded": True}`). Monkeypatch `cli._review_fetched` (`lambda *a, **k: True`) except in the review-abort case, and `run_scan` with a world whose contributors' collected files are monkeypatched per case. Cases: a changed hosted entry uploads and publishes a manifest whose entry carries the new hash while unchanged entries are byte-identical, with `runtime_hash` in the publish body; a changed github entry publishes new `directory_hash` and `files` and leaves `repo`/`skill_path`/`ref` untouched; everything matching prints "Already up to date." with no publish; non-owner (identity handle differs) exits 1 with the fork hint and no scan; `_review_fetched` returning False aborts with nothing published; declining the final confirm publishes nothing; `--yes` publishes without prompting.

- [ ] **Step 2: Verify failure, implement.**

```python
@loadout_app.command()
def update(
    ref: str = typer.Argument(..., help="owner/slug"),
    yes: bool = typer.Option(False, "--yes", help="skip the confirmation"),
) -> None:
    """Republish a loadout's entries from their local copies."""
    import copy

    from drskill import content, loadout_drift

    creds, base = _service_credentials()
    owner, slug = _parse_ref(ref)
    home = _home()
    try:
        identity = service.api_request("GET", "/api/v1/identity", token=creds["token"], base_url=base)
        if identity["user"]["handle"] != owner:
            typer.echo("You can only update your own loadouts; fork it first.")
            raise typer.Exit(1)
        data = service.api_request("GET", f"/api/v1/loadouts/{owner}/{slug}",
                                   token=creds["token"], base_url=base)
        current = data["loadout"].get("current_revision")
        if not current:
            typer.echo(f"{owner}/{slug} has no published revision.")
            raise typer.Exit(1)
        document = service.api_request(
            "GET", f"/api/v1/loadouts/{owner}/{slug}/revisions/{current['number']}",
            token=creds["token"], base_url=base, raw=True)
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)

    manifest = json.loads(document)
    world, _ = _scan_with_status(
        lambda p: run_scan(Path.cwd(), home, False, ledger.load_effective_config(Path.cwd(), home, False), progress=p)
    )
    statuses = loadout_drift.classify_entries(
        manifest.get("entries", []), list(world.contributors.values()))
    changed = [st for st in statuses if st.state == "changed"]
    for st in statuses:
        if st.state in ("missing", "unreadable"):
            typer.echo(f"  {st.entry['name']}: {st.state} locally; left as published")
    if not changed:
        typer.echo("Already up to date.")
        return

    manifest = copy.deepcopy(manifest)
    for st in changed:
        files = content.collect_files(st.contributor)
        if not _review_fetched(files, home, manifest=manifest,
                               selector=st.entry.get("selector"), name=st.entry["name"]):
            typer.echo("Update aborted.")
            raise typer.Exit(1)
        entry = next(e for e in manifest["entries"]
                     if e.get("selector") == st.entry.get("selector"))
        _refresh_entry(entry, files, st.contributor, creds, base)

    names = ", ".join(st.entry["name"] for st in changed)
    typer.echo(f"Changed: {names}")
    if not yes and not typer.confirm(
            f"Publish a new revision of {owner}/{slug} with "
            f"{len(changed)} updated skill{'s' if len(changed) != 1 else ''}?", default=False):
        raise typer.Exit(0)
    _, runtime_hash = service.canonical_manifest(manifest)
    try:
        data = service.api_request(
            "POST", f"/api/v1/loadouts/{owner}/{slug}/revisions",
            token=creds["token"], base_url=base,
            json_body={"manifest": manifest, "runtime_hash": runtime_hash})
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    revision = data["revision"]
    typer.echo(f"Published revision {revision['number']} ({revision['runtime_hash']}).")


def _refresh_entry(entry: dict, files: list[dict], contributor, creds: dict, base: str) -> None:
    from drskill import content

    source_type = entry.get("source_type")
    if source_type == "drskill":
        result = content.upload(files, creds["token"], base)
        entry["content_hash"] = result["content_hash"]
    elif source_type == "github":
        metadata = entry.setdefault("metadata", {})
        metadata["directory_hash"] = content.manifest_hash(files)
        metadata["files"] = sorted(f["path"] for f in files)
    else:
        entry["content_hash"] = contributor.content_hash
```

Upload failures propagate as ServiceError from `content.upload`; wrap the changed-entry loop in try/except ServiceError → `_echo_service_error` + exit 1. The `_review_fetched` call reuses the remediation helper verbatim, including the interactive-only ack loop and health-report refresh; check its non-tty behavior — it must not block when `interactive.can_interact()` refuses. Read the helper first: if the keypress loop is unconditional, gate it with `interactive.can_interact() is None` as part of this task (spec: findings display always, ack loop only interactively).

- [ ] **Step 3: Verify pass, full suite, commit** — `feat: add loadout update republishing`.

---

### Task 4: Verification and close-out

- [ ] **Step 1:** `uv run pytest -q`: zero failures.
- [ ] **Step 2: Live check** against the dev server with a sandboxed setup: run `loadout status` (expect the dev loadouts to report), edit `~/.agents/skills/scaffold-docs/SKILL.md`? No — do not touch the user's real skills. Instead: temp `DRSKILL_HOME` and a temp project with a scanned skill, publish via wizard-shaped API calls, run status (matches), modify the temp skill, status again (changed, exit 1), `loadout update` interactively, status (matches). Revoke the temp token and clean up afterward.
- [ ] **Step 3:** Report; branch menu.

## Coverage check against the spec

Matching and comparison rules → Task 1. Status forms, line states, hint, `--remote`, exit codes → Task 2. Update ownership gate, review reuse, per-type refresh, confirmation, publish, short-circuit → Task 3. Testing section maps onto each task's cases; the live walk → Task 4.
