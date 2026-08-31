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
