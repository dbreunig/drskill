# Loadout status and update design

Status: Approved (design approved in discussion 2026-09-02)
Date: 2026-09-02
Scope: drskill CLI only. `drskill loadout status` reports drift between
local skills and published revisions; `drskill loadout update` republishes
a loadout's existing entries from their local copies. No server changes.

## Goal

The publisher learns on their own machine that a skill no longer matches
what a loadout revision pins, and fixes it with one command. Status names
the drift; update publishes revision N+1 with refreshed hashes, content,
and health findings. Together they close the loop that install's drift
detection opens on consumer machines.

## Out of scope

- A lockfile. Matching is by name and hash against revisions fetched from
  the service; the durable binding arrives with the resolution phase.
- Adding or removing entries, visibility changes, or renames. Changing
  the roster stays the wizard's job.
- Updating loadouts the user does not own. The fork flow is how someone
  takes over another owner's loadout.
- Drift checks for mcp entries. v1 checks `kind: skill` entries and lists
  mcp entries as not checked.
- Folding a drift hint into `drskill scan`. Revisit after real usage.

## Matching local skills to entries

Both commands scan the way the wizard does (`pipeline.run_scan` over the
current directory and home) and match a revision entry to a local
contributor by `manifest_build.normalize_name(contributor.name) ==
entry["name"]`, considering only `kind: skill` contributors for skill
entries. When several contributors share the name, the one whose hash
matches the entry wins; otherwise the first in scan order is used and the
report notes the ambiguity. An entry with no local match is "not found on
this machine" — informational in status, left unchanged by update.

## Comparison rules, per entry type

- `drskill` (hosted): `content.manifest_hash(content.collect_files(c))`
  against the entry's `content_hash`.
- `github`: the same local hash against `metadata.directory_hash`. A
  legacy entry without one compares
  `resolution.content_hash(skill file text)` against the entry's
  `content_hash` instead.
- `local`: `contributor.content_hash` against the entry's `content_hash`.
- A contributor whose files cannot be read from disk reports as
  "unreadable", counted like "not found".

## drskill loadout status [ref]

- With no argument: every loadout the signed-in user owns
  (`GET /api/v1/loadouts`), skipping loadouts with no published revision.
  With `owner/slug`: that loadout, if viewable — a consumer can check
  their local copies against someone else's loadout, read-only.
- For each loadout it fetches the current revision manifest and prints a
  block: the loadout ref and revision number, then one line per entry —
  `matches`, `changed locally since publish`, `not found on this
  machine`, `unreadable`, or `not checked (mcp)`.
- When anything changed in a loadout the user owns, the block ends with
  `Run drskill loadout update <ref> to republish.`
- `--remote` additionally fetches each github entry's upstream tarball
  (same fetch, locate, and file-list rules as install) and reports
  `upstream has changed` when the extracted set no longer matches the
  entry. Off by default; one network fetch per github entry.
- Exit code: 1 when any `changed locally` (or `--remote` upstream drift)
  line was printed, 0 otherwise. Service errors exit 1 with the standard
  error output.

## drskill loadout update <ref>

1. Requires ownership: one `GET /api/v1/identity`; a non-owner gets
   "you can only update your own loadouts; fork it first" and exit 1.
2. Fetches the current revision manifest, scans, matches, and computes
   the changed set under the comparison rules. Nothing changed prints
   "Already up to date." and exits 0.
3. Each changed skill goes through the remediation review: lint findings
   displayed, and interactively the a/s/q keypress loop offers acks into
   the machine ledger, with q aborting the whole update. Non-interactive
   runs display findings without the ack loop and continue — publishing
   with findings is the publisher's call, exactly as it is in the create
   wizard. The manifest's health report, when present, is refreshed for
   the changed selectors from these runs.
4. The refreshed manifest keeps every entry in place and updates only the
   changed ones:
   - hosted: the local files are uploaded through `content.upload`
     (HEAD-skip and hash verification included) and the entry takes the
     new `content_hash`.
   - github: the entry takes a new `metadata.directory_hash` and
     `metadata.files` from the local copy; `repo`, `skill_path`, and
     `ref` are left as recorded.
   - local: the entry takes the contributor's `content_hash`.
5. One confirmation lists the changed entries ("Publish revision N+1 of
   <ref> with M updated skills? [y/N]", default no; `--yes` skips it),
   then the manifest publishes through the existing revisions endpoint
   with a client-computed runtime hash. The new revision number and
   runtime hash print on success.
6. An upload or publish failure prints the standard service error and
   exits 1 with nothing partially published (revision publication is
   atomic server-side; a content upload that succeeded before a later
   failure is harmless standalone content).

## Testing

- Matching: name normalization, hash-tiebreak among duplicates, missing
  and unreadable contributors, mcp entries skipped.
- Status: a fake API and a monkeypatched scan world covering all line
  states; the owned-loadouts sweep and the explicit-ref form; exit codes;
  `--remote` against the codeload stub reporting upstream drift and
  upstream match.
- Update: changed hosted entry re-uploads and republishes with the new
  hash; changed github entry gets a new directory_hash and files list;
  unchanged loadout short-circuits; non-owner refusal; q during review
  aborts with nothing published; `--yes` non-interactive publish;
  health-report refresh when the manifest carries one; the publish call
  carries a runtime_hash.
- Reuse the existing fixtures: the install tests' fake API shape, the
  wizard tests' world builders, and the review tests' key_source pattern.

## Risks

- Name-based matching can bind the wrong local skill when two skills
  share a normalized name and both drifted. The hash tiebreak covers the
  common case; the printed ambiguity note covers the rest. The lockfile
  phase replaces this heuristic with a real binding.
- `--remote` makes N network fetches and can rate-limit without a
  GITHUB_TOKEN. Accepted; it is opt-in and the fetch errors are per-entry.
- Update refreshes github entries from the local copy without touching
  `ref`, so a stale recorded ref plus a matching local copy can pin a
  hash upstream no longer serves at that ref. Install then reports
  upstream drift honestly, and remediation recovers. Accepted for v1.
