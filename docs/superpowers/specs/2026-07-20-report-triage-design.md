# Report presentation and ack routing

Date: 2026-07-20
Status: approved
Parent documents: `initial_design_doc.md`, `docs/superpowers/specs/2026-07-19-drskill-v0.1-design.md`, `docs/superpowers/specs/2026-07-20-tier3-injection-design.md`

This is the first of two specs for cycle 3 of the v0.2 release. The second spec is `2026-07-20-review-command-design.md`, which builds on this one. The `explain` command moves to cycle 4. One 0.2.0 release ships after all four cycles.

## Why

A scan of a real machine now produces nine findings, and the report treats them as one flat wall. Three findings with the same fix print separately. Findings on vendor system skills get the same prominence as findings on skills the user chose to install. The same harness list prints five times. Scan number five reads exactly like scan number one, even though the user already read eight of the findings. And acking a finding that lives entirely in the machine's global loadout writes the decision into the project ledger, so the user must ack the same finding again in every other project.

This cycle fixes the presentation and the ack routing. No check logic changes. Full evidence still prints for every finding, because the evidence is where judgment happens. The changes are order, labels, merging, and where decisions are stored.

## Report ordering

Errors print before warnings, unchanged. Within each severity section, findings sort by three keys:

1. New findings before seen findings. A finding is new when its fingerprint is not in the seen state (below).
2. Findings on user managed skills before findings on system skills (below).
3. Check id, then message, as the stable tiebreak.

The top of the report is always what changed, on things the user owns.

## New markers and the seen state

A new finding prints a bold `new` tag after its id, e.g. `[fe5b] new diverged-copies: ...`. The summary line gains the count, e.g. "0 errors, 9 warnings (2 new, 1 acknowledged)". The recap table at the end carries the same tag. Nothing is hidden or shortened based on seen status.

The seen state is machine memory, not a team decision, so it does not live in the ledger. It lives in `~/.drskill/state/`, one JSON file per project. The file name is a short hash of the realpath of the project root, and global mode uses the name `global`. The file maps each seen fingerprint to the date it was first shown.

A human format scan updates the state after rendering. A `--json` scan never touches it, so an agent polling the machine does not erase the user's new markers. State writes are best effort. If the directory cannot be written, every finding shows as new and the scan still succeeds.

## System skills

A contributor is classified as a system skill when any of its deployment paths contains a path segment named `.system`. This is the convention codex uses for the skills it installs itself. The classification is a labeled heuristic, and a harness data field can extend it later if other harnesses grow the same pattern.

Findings whose contributors are all system skills sort last within their severity section, and their harness line gains a `[system skill]` label. Nothing else changes. The findings still print in full and still count in the summary and exit codes.

## Harness line collapse

When a finding's harness list equals the full set of detected harnesses, the line prints as a count instead of a list, e.g. "harnesses: all 7 (copilot?)". The `?` facet markers survive inside the parentheses. Any smaller set prints as the full list, unchanged.

## Merged advisory findings

`missing-activation` and `generic-description` currently emit one finding per skill. Both become one finding per check, listing every offending skill with its path on its own line, following the pattern `description-overlap` set for clusters. The message states the count, e.g. "3 skills never say when to use them". One id acks the whole group.

The fingerprint hashes each member's name and description, sorted, so the ack resurfaces when any member's description changes or when membership changes. Fixing one of three skills changes membership, and the finding returns listing the remaining two. This matches the overlap cluster semantics that already shipped.

## The show command

```
drskill show <ref>... [--global] [--harness <id>]
```

`show` runs the scan pipeline and prints the referenced findings with their full evidence. A ref is either a 4 hex finding id or a check id, with the same resolution rules as `ack`. A check id prints every finding of that class. An unknown ref is a one line error and exit 1. `show` never writes the seen state.

## Ack routing

Today project mode reads one ledger, `./drskill.toml`, and every ack lands there. This cycle makes acks scope aware.

Reading. In project mode, config loading merges the ack lists from both ledgers: the project's `drskill.toml` and the machine's `~/.drskill.toml`. Budgets and thresholds stay strictly project local. Global mode still reads only the machine ledger.

Writing. `drskill ack` routes by the finding's contributors:

- If every contributor is user scope, the ack is written to `~/.drskill.toml`, and the CLI says so, e.g. "acknowledged globally (machine-level skills)". A finding that lives entirely in the global loadout is a machine decision, and writing home directory paths into a committed team file was the wrong default.
- If any contributor is project scope, the ack is written to the project ledger, unchanged from today.

Two flags override the routing in either direction. `--local` forces the project ledger, for "this is fine here, other projects decide for themselves". `--global` forces the machine ledger, including for mixed findings when the user has decided that exact content pair is fine everywhere. The fingerprint semantics are unchanged in every case: any content change resurfaces the finding.

CI is unaffected. A CI runner does not have the user's global skills installed, so the findings a global ack would silence never appear there.

## Out of scope

- The `review` command, which is the second spec of this cycle.
- Any change to check logic, severities, or fingerprint formulas outside the two merged advisory checks.
- Trust configuration in the ledger, e.g. a trusted paths list. The `.system` heuristic ships alone until there is a second real pattern.
- Collapsing or hiding findings based on seen status.

## Testing

- Ordering: a fixture world producing new, seen, user, and system findings in one scan, asserting the rendered order and tags.
- Seen state: first scan shows all new, second scan shows none new, a content edit resurfaces one as new. A `--json` scan between two human scans changes nothing. An unwritable state directory degrades to all new without error.
- System classification: a `.system` path segment classifies the contributor, and the label and sort position follow.
- Merged findings: the three skill fixture produces one finding, acking silences it, fixing one description resurfaces it with two members.
- Ack routing: a global-only finding acks into a temporary home ledger, a mixed finding acks into the project ledger, both flags override, and a project scan honors acks from both ledgers.
- `show`: by id, by check id, unknown ref errors, and no state write.
- Conformance and existing tests: the report format changes mean the rendered-output assertions get updated in the same commit as the change they cover.
