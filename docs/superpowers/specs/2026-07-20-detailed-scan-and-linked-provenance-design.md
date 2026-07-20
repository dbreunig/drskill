# Detailed scan output, harness scoping, and linked provenance

Date: 2026-07-20
Status: approved
Parent documents: `initial_design_doc.md`, `docs/superpowers/specs/2026-07-19-drskill-v0.1-design.md`

## Why

Three gaps surfaced while running v0.1 on a real machine.

- `scan` reports findings but gives no way to see the full picture of what was detected next to what is wrong with it. The user has to run `list` separately.
- Detection counts every tool whose config directory exists. On the test machine, 4 of 11 detected harnesses reach zero skills. Listing them is noise for a user who tried a tool once and never used it again.
- Skills that an installer symlinked into the canonical `.agents/skills` store show as `unmanaged`, because provenance only recognizes a project `skills-lock.json` entry or `gh skill` frontmatter. The symlink layout is itself evidence of installer management, so the label is misleading. On the test machine, 18 of 20 user level Claude Code skills were mislabeled this way.

## What changes

### 1. `scan --detailed`

`drskill scan --detailed` prints the normal findings report, then one skill table per harness. The tables are the same ones `list` prints. The rendering moves into a shared helper in `report.py` so `list` and `scan --detailed` call one implementation. `list` keeps its current behavior and flags.

Flag interplay on `scan`: `--json` wins over `--detailed`, so `scan --detailed --json` prints only the findings JSON, same as today. `--all` on `scan` is only meaningful together with `--detailed`; passing it without `--detailed` is accepted and does nothing.

### 2. Empty harnesses are hidden by default

A harness is empty when its effective set has no skills. `list` and `scan --detailed` skip empty harnesses by default and end with one line naming them, e.g. "4 more harnesses detected with no skills (antigravity, kiro-cli, opencode, qwen-code); show with --all". A new `--all` flag on both commands includes the empty harnesses in the tables.

The scan header line changes from "11 harnesses, 29 skills" to the split form "7 harnesses (4 more empty), 29 skills". When nothing is empty the parenthetical is omitted.

Detection itself does not change. Empty harnesses are still detected and still appear in `--json` output and in `World.harnesses`. This is a display rule, not a detection rule.

### 3. `--harness <id>` on scan

`scan` gains the `--harness <id>` option that `list` already has. It scopes the whole pipeline, not just the printed output: only the named harness is detected, discovered, resolved, and checked. The result answers "what does this harness see". Cross harness findings such as `exact-duplicate` across two harnesses do not appear in a scoped scan, which is correct for that question.

Both `scan --harness` and `list --harness` validate the id. An unknown id prints an error naming the valid ids from the harness table and exits 1. Today `list --harness bogus` prints nothing, which reads as "no skills" instead of "no such harness".

A valid id whose harness is not detected on this machine is not an error. The scan runs against its search paths anyway, finds nothing, prints a note that the harness was not detected, and exits 0. This keeps the flag useful for checking a tool before installing it.

### 4. `linked` provenance

`Provenance.kind` gains a fourth value, `linked`. Resolution assigns it when both of these hold:

- the contributor was not already classified `skills-lock` or `gh-skill`
- the contributor's realpath lies under a directory named `.agents/skills`

This covers both a symlink from a harness directory into the store and a skill discovered directly in the store. `Provenance.source` stays None for `linked`. `list` shows `linked` in the source column. `unmanaged` goes back to meaning a hand dropped directory with no known manager.

The classification is a heuristic about layout, not a claim about which installer created the link. The docstring says so.

## Out of scope

- Reading any global or store level lockfile to name the installer behind a `linked` skill. No such manifest existed on the test machine; revisit if `npx skills` ships one.
- Changing detection rules or markers.
- The `--detailed` flag on `list` (redundant; `list` is already the detailed view).

## Testing

- Shared renderer: `list` output and the table section of `scan --detailed` come from one helper; a test asserts both paths render a fixture harness identically.
- Empty harness filtering: fixture with one populated and one empty harness; default output hides the empty one and prints the closing line; `--all` shows it; the scan header shows the split count.
- Scoped scan: fixture where two harnesses each load one copy of identical content; unscoped scan reports `exact-duplicate`; `scan --harness <one>` reports nothing and its `--json` output only contains that harness.
- Unknown id: `scan --harness bogus` and `list --harness bogus` exit 1 and name the valid ids.
- `linked`: a skill symlinked from `.claude/skills` into `.agents/skills` and a skill living directly in `.agents/skills` both classify `linked`; a plain directory in `.claude/skills` stays `unmanaged`; a lockfile entry still wins as `skills-lock`.
- Conformance: existing cases must stay green; add expectations only if a case's behavior is affected by the display changes (none should be, since conformance asserts findings, not tables).
