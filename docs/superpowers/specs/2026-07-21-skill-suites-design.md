# Skill suites in list

Date: 2026-07-21
Status: approved
Parent documents: `initial_design_doc.md`, `docs/superpowers/specs/2026-07-19-drskill-v0.1-design.md`

## Why

A user's skills come from somewhere. Many arrive as a set: the superpowers plugin ships a dozen skills, a GitHub repo ships a few, and a marketplace ships more. Once installed, that grouping is lost. `drskill list` shows each skill on its own row, and nothing says which skills came together. A user who wants to know "which of these are my superpowers skills" cannot tell. This cycle adds a suite column to `list` so the grouping is visible again.

The wrinkle is that the origin is often not in the installed skill. A plugin skill copied into a shared store keeps its name and content but not its plugin path. So drskill recovers the suite from machine data that still holds it: the plugin caches on disk, and the project lockfile.

## Detection

A new module, `src/drskill/suites.py`, builds a registry from two read-only sources.

Plugin caches. Claude Code keeps installed plugins under `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, and a plugin's skills live in its `skills/<name>/SKILL.md`. drskill walks every cached plugin, and for each plugin skill it computes the same normalized content hash it computes for an installed skill. The result is a map from content hash to a suite label, the plugin name. Every cached version is indexed, so a skill that matches any version is recognized. Matching on content, not path, is what recovers a plugin skill that was copied flat into a shared store: the copy has the same content, so it maps to the same plugin.

Lockfile. Each `skills-lock.json` entry names a skill and carries a `source`, e.g. `dbreunig/scaffold-docs-skill`. That source is the suite for the skill it names.

## Assigning a suite

After the world is built, each contributor is matched in this order:

1. If its content hash is in the plugin registry, its suite is that plugin's name. This is exact proof that the installed skill is the plugin's skill.
2. Otherwise, if the skill is named in `skills-lock.json` with a source, its suite is that source.
3. Otherwise it has no suite.

`Contributor` gains an optional field `suite: str | None = None`, populated in the pipeline from the registry. A skill edited after install no longer matches the plugin by content, so it shows no suite rather than a wrong one. This follows the existing rule that drskill labels what it can verify and stays quiet otherwise.

## Display

`drskill list` and `drskill list --tokens` gain a `suite` column. It shows the plugin name for a plugin match, the `owner/repo` string for a lockfile match, and nothing when the suite is unknown. The column is escaped like every other cell. Nothing else in `list` changes, and the scan report does not change.

## Structure

- `src/drskill/suites.py`: the plugin cache walker, the lockfile reader, the content hash registry, and the per contributor lookup.
- `src/drskill/models.py`: the `suite` field on `Contributor`.
- `src/drskill/pipeline.py`: populate `suite` after the world is built.
- `src/drskill/report.py`: the `suite` column in the harness tables.

## Out of scope

- Guessing a suite from a directory path or a name prefix. Those are guesses that mislabel skills that happen to share a folder, and drskill labels only what it can verify.
- A grouped `--by-suite` view. The column is enough for this cycle.
- Any check based on suite membership, e.g. a partial suite or a version drift warning.
- Plugin systems of harnesses other than Claude Code. Claude Code is the one with an on disk plugin registry today.
- Changing the scan report.

## Testing

- A fixture plugin cache with one plugin and two skills, and a flat copy of one of those skills elsewhere. The copy, matched by content hash, shows the plugin as its suite.
- A fixture `skills-lock.json` with a `source`. The named skill shows that source as its suite.
- A skill matching neither shows no suite.
- A skill whose content was edited after install no longer matches the plugin and shows no suite.
- The plugin registry indexes every cached version, so a skill matching an older cached version is still recognized.
- `list` and `list --tokens` render the suite column with the expected values, and a suite value with rich markup renders escaped.
- Every test sets `DRSKILL_HOME`.
- The real machine gate: `drskill list` on the author's loadout, confirming the superpowers skills show `superpowers` in the suite column.
