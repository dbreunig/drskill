# Loadout create wizard — design

Status: Approved (design approved in discussion 2026-08-31)
Date: 2026-08-31
Scope: CLI-only (this repo). An interactive path for `drskill loadout create`
that scans the current project, lets the user select which active skills to
include, and publishes the selection as revision 1. Bridges the scanner side
of the CLI (resolution, contributors) to the service side (create + publish).

## Goal

`drskill loadout create textbook --from-project` scans the project, shows one
selection list of the skills that are active (project scope and user scope as
separate sections, shown together), lets the user toggle what to include,
summarizes the result, and on confirm creates the loadout and publishes the
selection as revision 1, reporting each step separately.

## Out of scope

- Hosting skill content on the service (deferred; see Decisions 5). Skills
  with no tracked source become `local_only` entries with a warning.
- `drskill use` / installing loadouts into a harness.
- Editing an existing loadout's content interactively (wizard is create-only).
- `harness_mappings` content (published empty in v1; deferred until `use`
  exists to consume them).
- Any new dependency. The selection UI is plain typer/rich prompt loops.

## Decisions (from the approved design discussion)

1. **Wizard is opt-in via `--from-project`.** Plain `create <slug>` keeps its
   current non-interactive behavior. `--from-project` with stdin or stdout
   not a TTY exits 1 with a plain message instead of hanging.
2. **The wizard selects harness-agnostic contributors, not harness files.**
   The scanner's resolution step already collapses per-harness sightings
   into one `Contributor` with a deployment list. Each row shows harness
   badges, e.g., `citation-style  [claude-code, pi]`, as information only.
   No harness step in the wizard. `--harness <id>` optionally filters the
   list to contributors with at least one deployment in that harness
   (option 2 from the discussion).
3. **Both scopes shown together, as separate sections.** Project scope
   first, user scope below it, in one list with one numbering, so nothing
   looks missing. Project-scope rows start selected; user-scope rows start
   unselected (the loadout describes this project; machine-wide skills are
   opt-in).
4. **Local-only skills are included with a warning.** Contributors with no
   tracked source map to `local_only: true` entries. The summary states
   plainly that these block making the loadout public later. Private
   loadouts (which create makes) accept them today.
5. **Hosting is deferred.** Turning the service into a content registry
   (blob upload, a `drskill` source type, serving skill bodies) is its own
   future design, probably alongside `drskill use`.
6. **Create-then-publish, reported as two steps.** The earlier argument
   against content-in-create was unclear failure states; the wizard
   resolves it because the user confirmed exactly what will publish, and
   failures stay legible: if create fails, stop; if publish fails, say the
   loadout was created empty and print the retry command
   (`drskill loadout publish <ref> <manifest>`), which works because the
   wizard can save the generated manifest.
7. **`--manifest-out <file>` saves the generated manifest** (optional). On
   publish failure the wizard always writes the manifest to a temp file and
   names it in the retry message, so the user never loses the selection.

## Command surface

```
drskill loadout create <slug> [--name TEXT] [--description TEXT]
    --from-project [--harness ID] [--manifest-out FILE]
```

`--harness` and `--manifest-out` are only meaningful with `--from-project`;
using them without it exits 1 with a plain message.

## Flow

1. Credentials check first (`_service_credentials`), before any scanning, so
   a signed-out user fails fast with the login hint.
2. Scan the current directory with the same pipeline `drskill scan` uses
   (`pipeline.run_scan`), quietly (no findings output; the wizard only needs
   the resolved `World`).
3. Build candidate rows from `world.contributors`:
   - Skip `system` contributors (harness-vendored skills).
   - Apply the `--harness` filter when given (unknown harness id exits 1,
     reusing the existing harness validation).
   - Row data: name, kind (skill or mcp tool), scope, harness badges from
     deployments, source summary (provenance source, or "local only").
   - Sections: project scope, then user scope. One shared numbering.
4. Selection loop (plain prompt, no new dependencies):
   - Print the sectioned list with `[x]`/`[ ]` markers and badges.
   - Prompt: numbers toggle (comma or space separated), `a` selects all,
     `n` clears all, empty input accepts the current selection.
   - Zero selections on accept exits 1 with "Nothing selected."
5. Summary: the selected entries with kind, name, and source, a count line,
   and, when any entry is local-only, the warning:
   "N skills have no tracked source and will be marked local-only. That is
   fine for a private loadout, but it blocks making this loadout public."
6. Confirm: "Create <handle>/<slug> and publish these N entries as
   revision 1? [y/N]". Decline exits 0 without any server call.
7. Execute:
   - Build the manifest (below), write it to `--manifest-out` if given.
   - Create the loadout (existing create request). Failure: print the
     validation messages and exit 1. Nothing was published.
   - Publish the manifest with the client-computed hash (same path as
     `loadout publish`). Failure: write the manifest to a temp file (unless
     `--manifest-out` already saved it), then print:
     "Created <ref>, but the publish failed:" + the validation messages +
     "The loadout exists and is empty. Fix the manifest and run:
      drskill loadout publish <ref> <manifest path>". Exit 1.
   - Success: print the created ref, the section summary (e.g., "5 entries,
     1 local-only"), and "Published revision 1 (<hash>)".

## Contributor-to-entry mapping

One manifest entry per selected contributor:

- `kind`: contributor kind `skill` → `"skill"`, `mcp_tool` → `"mcp"`.
- `name`: the contributor name, normalized to satisfy the server's selector
  rule (`[a-z0-9][a-z0-9._-]*`): lowercase, invalid characters replaced with
  `-`, collapsed. When normalization changes the name, the summary notes it,
  e.g., `My Skill → my-skill`.
- `selector`: `"<kind>:<normalized name>"`. Duplicate selectors after
  normalization get a numeric suffix (`-2`) and a note, since the server
  rejects duplicates.
- Source, from the contributor's provenance:
  - provenance kind `gh-skill` or `skills-lock` → `source_type: "github"`,
    `source_reference:` the provenance source string, `local_only: false`.
  - provenance kind `plugin` → `source_type: "plugin"`, `source_reference:`
    the provenance source or the suite name, `local_only: false`.
  - provenance kind `linked` or `unmanaged`, or a missing source →
    `source_type: "local"`, `source_reference:` the skill path (project- or
    home-relative as the scanner records it), `local_only: true`.
- `content_hash`: the contributor's content hash (already `sha256:` form).
- `source_version`: omitted in v1 (nullable server-side).
- `metadata`: `{}`.

Manifest envelope: `schema_version: 1`, `reproducible: false` (the wizard
does not verify sources), the entries, and `harness_mappings: []`
(deferred; see Out of scope).

The mapping lives in `service.py` (or a small new module) as a pure function
`contributors_to_manifest(contributors) -> dict` so it is unit-testable
without any UI or network.

## Testing

- Unit tests for the mapping: each provenance kind, name normalization,
  duplicate-selector suffixing, local-only flagging, mcp kind, envelope
  shape, and that the result passes `canonical_manifest` (hashable).
- Wizard flow tests with CliRunner `input=` strings and a fabricated `World`
  (monkeypatched `run_scan`): toggle then accept publishes the right subset;
  sections render project before user scope; preselection defaults; `a`,
  `n`, empty-accept, zero-selection exit; decline-at-confirm makes no
  server call; `--harness` filters; unknown `--harness` exits 1.
- Two-step failure tests against monkeypatched requests: create failure
  stops before publish; publish failure prints the created-but-empty
  message with a manifest path that exists and re-publishes cleanly.
- Non-TTY guard: `--from-project` with a non-TTY stdin exits 1 with the
  plain message (CliRunner can simulate this; if not cleanly, test the
  guard function directly).
- Plain `create <slug>` behavior unchanged (existing tests stay green).

## Risks

- Contributor names can normalize into collisions or unrecognizable
  selectors. The suffix rule plus summary notes keeps publishes valid and
  visible; worst case the user edits with `--manifest-out` and publishes
  manually.
- Provenance source strings were built for scan reports, not for install
  references. v1 copies them as-is into `source_reference`; if `drskill
  use` later needs stricter references, that lands with the `use` design.
  The server deliberately does not constrain `source_type` yet.
- `run_scan` can be slow on big projects. The wizard shows the same status
  spinner scan uses, or at minimum a "scanning..." line before the list.

## Revisions after the live walk-through (2026-08-31)

Drew ran the wizard against a real project (133 rows) and gave three pieces
of feedback, which revise the decisions above:

1. **Scan progress.** The wizard shows the same live status spinner
   `drskill scan` uses, updating with the scan's progress messages, instead
   of a single static "Scanning..." line.
2. **Harness selection comes first (revises Decision 2's "no harness
   step").** With user-scope skills active across many harnesses, the flat
   list is too long. After the scan, when the candidate rows span more than
   one harness, the wizard asks which harness to draw from (numbered
   choices plus "all") before rendering the list. `--harness` skips the
   question; a single-harness world never sees it. Badges still show every
   harness a skill is deployed to.
3. **Cross-harness dedup (new).** Resolution collapses skills only when
   harnesses share the same file; plugin installs materialize per-harness
   copies with distinct paths, so the same skill at the same version
   appeared once per harness. The wizard merges candidate rows before
   rendering: tracked skills merge on (kind, normalized name, provenance
   source string, which carries the version); local-only skills merge on
   (kind, normalized name, content hash). A merged row unions its harness
   badges across the group, sits in the project section when any member is
   project scope (and is preselected accordingly), and publishes one entry
   from its best member, preferring a tracked source over local-only.

## Second walk-through revisions (2026-08-31)

4. **Merge on name alone.** The provenance-keyed dedup still showed the same
   skill twice when a local copy and a plugin-delivered copy coexist. Rows
   now merge on (kind, normalized name). The representative, which is what
   gets published, is a tracked member when the group has one (preferring a
   member with a provenance source), else the local copy. Badges union
   across the whole group. Rationale: a loadout wants one entry per skill,
   and the server rejects duplicate selectors anyway.
5. **Arrow-key selection.** The list is navigated with up/down arrows or
   j/k, space toggles, `a`/`n` select all/none, enter accepts, q aborts.
   Long lists render a scrolling window. Implemented on stdlib termios raw
   mode with no new dependency; when raw mode is unavailable (non-POSIX or
   odd terminals), the wizard falls back to the existing numbered prompt
   loop automatically.
