"""Interactive create flow: scan the project, pick skills, create + publish.

Selection happens over harness-agnostic contributors (the scanner already
collapses per-harness sightings); harness names appear only as badges and as
an optional filter. See docs/superpowers/specs/2026-08-31-loadout-wizard-design.md.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import questionary
import typer
from rich.console import Console
from rich.markup import escape

from drskill import content, manifest_build, pipeline, service
from drskill.models import Contributor

# The wizard's own Console instance, not cli.py's: cli.py imports this module
# lazily inside `create` (to avoid a cycle at module-import time), so a
# top-level `from drskill.cli import console` here would risk a circular
# import if cli.py is ever mid-import when this module loads. A private
# instance costs nothing and sidesteps the question entirely.
console = Console()

_VERSION_SUFFIX = re.compile(r"==.*$")


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
        # Defense-in-depth: cli.py already checks _stdin_is_tty() before
        # ever calling run(), so this should be unreachable in practice.
        typer.echo("The interactive picker needs a terminal.")
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
        chosen = harness
        rows = [row for row in candidates if chosen in row.harnesses]
    elif len(all_harnesses) > 1:
        console.print("\n[bold]Pick a harness[/bold]")
        chosen = _choose_harness(all_harnesses)
        rows = candidates if chosen is None else [
            row for row in candidates if chosen in row.harnesses
        ]
    else:
        chosen = None
        rows = candidates

    if not rows:
        typer.echo("No skills found in this project to include.")
        raise typer.Exit(1)

    heading = f"Pick skills — from {chosen}" if chosen is not None else "Pick skills"
    console.print(f"\n[bold]{escape(heading)}[/bold]")
    selected_rows = _choose_skills(rows, chosen)

    selected = [row.contributor for row in selected_rows]
    if not selected:
        typer.echo("Nothing selected.")
        raise typer.Exit(1)

    hosted = _offer_registry(selected, creds, base_url, home)
    manifest, notes = manifest_build.contributors_to_manifest(selected, hosted=hosted)
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
    # Rows merge on (kind, normalized name) alone: a local copy and a
    # plugin-delivered copy of the same skill are still the same skill (the
    # server rejects duplicate selectors anyway), and resolution only
    # collapses skills that share a file, so a plugin install still
    # materializes one contributor per harness for the wizard to re-merge
    # here. The representative (see _pick_representative) prefers a tracked
    # member so the published entry carries real provenance when the group
    # has one.
    return (c.kind, manifest_build.normalize_name(c.name))


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


def _choose_harness(harness_ids: list[str]) -> str | None:
    choices = sorted(harness_ids) + ["All harnesses"]
    answer = questionary.select(
        "Which harness should this loadout draw from?",
        choices=choices,
    ).ask()
    if answer is None:  # Ctrl-C
        typer.echo("Aborted.")
        raise typer.Exit(0)
    return None if answer == "All harnesses" else answer


def _label_width(rows: list[_Row]) -> int:
    # A shared column width for the whole visible list, so names line up:
    # floored at 24 (comfortable for typical short skill names), capped at
    # 34 so one outlier long name doesn't drag every other row's padding
    # out with it.
    if not rows:
        return 24
    longest = max(len(row.contributor.name) for row in rows)
    return min(max(24, longest), 34)


def _row_label(row: _Row, chosen_harness: str | None, width: int = 24) -> str:
    name = row.contributor.name.ljust(width)
    source = row.contributor.source.source or "local only"
    source = _VERSION_SUFFIX.sub("", source)
    label = f"{name}  {source}"
    if chosen_harness is None:
        harnesses = row.harnesses
        if not harnesses:
            pass
        elif len(harnesses) <= 2:
            label += f"  [{', '.join(harnesses)}]"
        else:
            label += f"  [{harnesses[0]} +{len(harnesses) - 1}]"
    return label


def _choose_skills(rows: list[_Row], chosen_harness: str | None) -> list[_Row]:
    width = _label_width(rows)
    choices: list = []
    current_scope = None
    for row in rows:
        if row.scope != current_scope:
            # Only insert a divider at an actual transition between scope
            # groups, not before the very first group (there's nothing above
            # it to divide from, and the "Pick skills" heading already
            # labels the list).
            if current_scope is not None:
                choices.append(questionary.Separator(
                    "— Project scope —" if row.scope == "project" else "— User scope —"
                ))
            current_scope = row.scope
        choices.append(questionary.Choice(
            title=_row_label(row, chosen_harness, width), checked=row.selected, value=row,
        ))
    answer = questionary.checkbox(
        "Pick skills",
        choices=choices,
        instruction="(space to toggle, enter to accept, ctrl-c to abort)",
    ).ask()
    if answer is None:  # Ctrl-C
        typer.echo("Aborted.")
        raise typer.Exit(0)
    return answer



def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size / 1.0:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} MB"


def _offer_registry(selected: list[Contributor], creds: dict, base_url: str,
                    home: Path) -> dict[str, dict]:
    """Offer to publish untracked skills to the user's registry so the
    loadout can install them anywhere. Returns contributor id ->
    {"content_hash", "source_reference"} for the published skills."""
    local = [c for c in selected if manifest_build.is_local(c)]
    if not local:
        return {}

    console.print(
        f"\n[bold]{len(local)} selected "
        f"skill{'s' if len(local) != 1 else ''} exist only on this machine:[/bold]"
    )
    for contributor in local:
        # Sizes from the scan, not the disk: nothing is read before consent.
        size = len(contributor.body.encode()) + sum(b.size for b in contributor.bundled_files)
        count = 1 + len(contributor.bundled_files)
        plural = "s" if count != 1 else ""
        console.print(f"  {escape(contributor.name)}  ({count} file{plural}, {_human_size(size)})")

    if not typer.confirm(
        "Publish them to your registry so this loadout can install them anywhere?",
        default=False,
    ):
        return {}

    from drskill import cli as cli_mod

    hosted: dict[str, dict] = {}
    for contributor in local:
        try:
            files = content.collect_files(contributor)
        except OSError as err:
            typer.echo(f"Could not read {contributor.name}: {err}")
            raise typer.Exit(1)
        name = manifest_build.normalize_name(contributor.name)
        description = contributor.frontmatter.get("description")
        description = description if isinstance(description, str) else None
        result = cli_mod._skill_publish_flow(
            files, name, description, None, creds, base_url, home)
        if result is None:
            typer.echo(f"Publishing {contributor.name} was blocked; "
                       "fix or ack its findings and retry.")
            raise typer.Exit(1)
        hosted[contributor.id] = {"content_hash": result["content_hash"],
                                  "source_reference": result["reference"]}
    return hosted

def _print_summary(manifest: dict, notes: list[str]) -> None:
    entries = manifest["entries"]
    local_only = [entry for entry in entries if entry["local_only"]]
    heading = f"Summary — {len(entries)} entries"
    if local_only:
        heading += f", {len(local_only)} local-only"
    console.print(f"\n[bold]{escape(heading)}[/bold]")
    for entry in entries:
        marker = "  local only" if entry["local_only"] else f"  {entry['source_reference']}"
        typer.echo(f"  {entry['selector']}{marker}")
    for note in notes:
        console.print(f"[dim]  note: {escape(note)}[/dim]")
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
    ref = f"{loadout['owner']}/{loadout['slug']}"
    console.print(f"\n[green]✓[/green] Created {escape(ref)}")
    return ref


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
    console.print(
        f"[green]✓[/green] Published revision {revision['number']} "
        f"({escape(revision['runtime_hash'])})"
    )
