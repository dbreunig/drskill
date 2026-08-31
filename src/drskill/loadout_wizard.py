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
from rich.console import Console
from rich.markup import escape

from drskill import manifest_build, pipeline, service
from drskill.models import Contributor

# The wizard's own Console instance, not cli.py's: cli.py imports this module
# lazily inside `create` (to avoid a cycle at module-import time), so a
# top-level `from drskill.cli import console` here would risk a circular
# import if cli.py is ever mid-import when this module loads. A private
# instance costs nothing and sidesteps the question entirely.
console = Console()


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


@dataclass
class _Row:
    contributor: Contributor  # representative: display name/source and the entry published
    harnesses: list[str]  # union of harness ids across every merged member
    scope: str  # merged section scope: "project" if any member is project-scoped
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

    with console.status("[bold]starting[/bold]", spinner="dots") as status:
        # Scan ALL harnesses even when --harness filters the list: the filter
        # narrows which rows appear, but badges must still show every harness a
        # skill is deployed to (a harness-scoped scan would lose that).
        world, _findings = pipeline.run_scan(
            Path.cwd(), home, progress=lambda m: status.update(f"[bold]{escape(m)}[/bold]")
        )

    candidates = _build_rows(world)
    if not candidates:
        typer.echo("No skills found in this project to include.")
        raise typer.Exit(1)

    all_harnesses = sorted({h for row in candidates for h in row.harnesses})
    if harness is not None:
        rows = [row for row in candidates if harness in row.harnesses]
    elif len(all_harnesses) > 1:
        chosen = _select_harness(all_harnesses)
        rows = candidates if chosen is None else [
            row for row in candidates if chosen in row.harnesses
        ]
    else:
        rows = candidates

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

    if not typer.confirm(
        f"Create '{slug}' and publish these "
        f"{len(manifest['entries'])} entries as revision 1?", default=False
    ):
        raise typer.Exit(0)

    if manifest_out:
        manifest_out.write_bytes(json.dumps(manifest, indent=2).encode())
        typer.echo(f"Wrote manifest to {manifest_out}")

    ref = _create_loadout(slug, name, description, creds, base_url)
    _publish(ref, manifest, manifest_out, creds, base_url)


def _is_tracked(c: Contributor) -> bool:
    # Mirrors manifest_build's own local_only test: a tracked contributor has
    # a source kind that maps to a real source type AND a source string.
    return manifest_build._SOURCE_TYPES.get(c.source.kind) is not None and bool(c.source.source)


def _group_key(c: Contributor) -> tuple:
    # Resolution collapses skills only when harnesses share the same file;
    # plugin installs materialize per-harness copies with distinct paths, so
    # the same skill at the same version appears once per harness. Group
    # those back together before rendering: tracked skills merge on (kind,
    # normalized name, provenance source string, which carries the version);
    # local-only skills merge on (kind, normalized name, content hash).
    name = manifest_build.normalize_name(c.name)
    if _is_tracked(c):
        return ("tracked", c.kind, name, c.source.source)
    return ("local", c.kind, name, c.content_hash)


def _pick_representative(members: list[Contributor]) -> Contributor:
    # Prefer a tracked member over local-only when the group has one; ties
    # break on id for determinism.
    tracked = [m for m in members if _is_tracked(m)]
    pool = tracked or members
    return sorted(pool, key=lambda m: m.id)[0]


def _build_rows(world) -> list[_Row]:
    groups: dict[tuple, list[Contributor]] = {}
    order: list[tuple] = []
    for c in world.contributors.values():
        if c.system:
            continue
        key = _group_key(c)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    rows = []
    for key in order:
        members = groups[key]
        representative = _pick_representative(members)
        harnesses = sorted({d.harness for m in members for d in m.deployments})
        scope = "project" if any(m.scope == "project" for m in members) else "user"
        rows.append(
            _Row(contributor=representative, harnesses=harnesses, scope=scope,
                 selected=(scope == "project"))
        )
    rows.sort(key=lambda row: (row.scope != "project", row.contributor.name.lower()))
    return rows


def _select_harness(harnesses: list[str]) -> str | None:
    typer.echo("Which harness's skills should this loadout draw from?")
    for index, h in enumerate(harnesses, start=1):
        typer.echo(f"  {index}  {h}")
    typer.echo("  a  all harnesses")
    while True:
        raw = typer.prompt("Choice").strip().lower()
        if raw == "a":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(harnesses):
            return harnesses[int(raw) - 1]
        typer.echo("Enter a number from the list above, or 'a' for all harnesses.")


def _render(rows: list[_Row]) -> None:
    current_scope = None
    for index, row in enumerate(rows, start=1):
        if row.scope != current_scope:
            current_scope = row.scope
            typer.echo(f"\n{'Project scope' if current_scope == 'project' else 'User scope'}")
        badge = f"  [{', '.join(row.harnesses)}]" if row.harnesses else ""
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
