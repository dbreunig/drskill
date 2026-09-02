# External source install design

Status: Approved (design approved in discussion 2026-09-02)
Date: 2026-09-02
Scope: Mostly drskill CLI. Publish records fetch coordinates and a
directory hash for external entries; `drskill loadout install` fetches
`github` entries from GitHub, verifies them, and installs them alongside
hosted entries. A verification mismatch starts an interactive remediation
flow: review, then republish or fork. The drskill-web service gains one
endpoint, fork over the API.

## Goal

`drskill loadout install owner/slug` installs every installable entry in a
revision, not only the hosted ones. A `github` entry is fetched from the
upstream repository, verified against hashes recorded at publish, and
written through the same safe install path hosted content uses. Entries
published before this feature still install when their SKILL.md matches,
with a stated caveat.

## Out of scope

- Server changes beyond the fork endpoint. Entry `metadata` is already
  free-form, and the server validates nothing new about it.
- Per-file hashes in entry metadata (they would enable file-level diffs
  at review time; the review runs the checks instead).
- Escrow (uploading copies of external skills to the service). Considered
  and set aside for licensing reasons.
- Sources other than GitHub. `source_type` values besides `drskill` and
  `github` remain uninstallable and are reported as skipped.
- Lockfile writing. Locking belongs to the future resolution phase.

## Publish side: recorded metadata

`manifest_build.contributors_to_manifest` writes these keys into the
`metadata` object of every entry whose `source_type` is `github`:

- `directory_hash`: the manifest hash of the skill's files, computed
  from disk with `content.manifest_hash(content.collect_files(c))`. This
  is the same algorithm hosted content uses, so one identity verifies
  everything. Omitted when the files cannot be read.
- `files`: the sorted relative paths the hash covers. Install extracts
  only these paths from the repository, so housekeeping files around a
  root-level skill (README, dotfiles, a docs directory) are never treated
  as part of the skill and never cause false drift. Recorded and omitted
  together with `directory_hash`.
- `repo`: `owner/repo`, parsed from the provenance source string.
- `skill_path`: the skill directory's path inside the repo, derived from
  the ecosystem lockfile's `skillPath` (its value points at SKILL.md; the
  recorded value is its parent directory, empty string for the repo root).
  Omitted when unknown.
- `ref` and `tree_sha`: copied from gh-skill frontmatter when present.

To carry `skillPath` from the scanner to the manifest builder, the
`Provenance` model gains optional `path` and `ref` fields. The pipeline's
lockfile pass fills `path` from `skillPath`; frontmatter provenance fills
`ref` from the `ref` key. Existing Provenance consumers are unaffected
because both fields default to None.

## Install side: fetch

`content.py` gains a fetch path used by `loadout install` for `github`
entries:

1. Coordinates. `repo` comes from `metadata.repo`, else from
   `source_reference` with any `@version` suffix stripped. The ref is the
   first of `metadata.ref`, the entry's `source_version`, or `HEAD` (the
   default branch). A source that does not parse as `owner/repo` makes the
   entry uninstallable, reported and skipped.
2. Download. `GET https://codeload.github.com/{repo}/tar.gz/{ref}` over
   https with a timeout and a 100 MB compressed size cap enforced while
   streaming. When the `GITHUB_TOKEN` environment variable is set, it is
   sent as a bearer Authorization header (private repos, rate limits).
   Only the codeload.github.com host is ever contacted.
3. Locate. The tarball's single top-level directory is stripped. When
   `skill_path` is recorded, the skill directory is exactly that path.
   Otherwise the fetcher lists every directory containing a SKILL.md and
   picks the one whose contents match the verification hash (the
   `directory_hash`, or failing that the SKILL.md `content_hash`). Zero or
   several matches make the entry uninstallable, reported and skipped.
4. Extract. When the entry records a `files` list, only those paths are
   taken from the skill directory; other repository files are ignored,
   and an entry none of whose recorded files exist is uninstallable.
   Members under the skill directory pass the same gate as
   hosted downloads: regular files only, no symlinks or links or devices,
   safe relative paths, at most 200 files and 20 MB unpacked. The result
   is the same `[{path, data, executable}]` shape the rest of content.py
   uses.

## Verification policy

- An entry with `metadata.directory_hash` must match it exactly. A
  mismatch starts the remediation flow below; nothing installs
  unverified.
- An entry without `directory_hash` (published before this feature) is
  verified by its existing `content_hash` against the fetched SKILL.md
  text, normalized the way the scanner normalizes
  (`resolution.normalize_content` under `resolution.content_hash`). On a
  match the install proceeds and prints one caveat naming the entry:
  bundled files are unverified. On a mismatch the entry enters the same
  remediation flow.
- There is no flag to skip verification. The only way past a mismatch is
  a new published revision that pins what the user reviewed.

## Mismatch remediation flow

A mismatch prints: "The remote skill has been updated since this loadout
was created and the original version was not pinned." What follows
depends on whether the signed-in user owns the loadout. The CLI learns
its own handle from one `GET /api/v1/identity` call, made only when a
mismatch occurs.

The user's loadout:

1. The CLI holds the fetched new version, so it reviews it in place: it
   runs the standard skill checks on the fetched content and displays the
   findings the way `drskill scan` does. The user may ack findings, and
   acks are recorded in the machine ledger with the normal semantics.
2. It then prompts: "Publish revision N+1 of <owner>/<slug> with the
   updated skill and install it? [y/N]", default no.
3. On yes, it takes the current revision's manifest, updates that entry's
   `metadata.directory_hash` and `metadata.files` from the fetched set
   (and `ref` when newly known),
   refreshes that entry's findings in the manifest's `health_report` from
   the review run, publishes through the existing revisions endpoint, and
   installs the now-verified content. On no, nothing is written or
   published.

Someone else's loadout:

1. The CLI prompts: "Fork <owner>/<slug> to your account and review the
   updated skill? [y/N]", default no.
2. On yes, it calls the new fork endpoint. The fork keeps the origin's
   slug and name by default; on a slug collision the CLI prompts for a
   different slug and retries. The fork is private and carries the
   origin's current revision, so its entries are identical.
3. The CLI then continues the owner flow against the fork: review with
   checks and acks, publish the fork's next revision, install.

Non-interactive runs (`--yes`, or stdin is not a terminal) skip
remediation entirely: the entry fails with the message above plus a hint
to rerun interactively, and the command continues with other entries.

## Server: fork over the API

drskill-web gains `POST /api/v1/loadouts/:owner_handle/:slug/fork` under
the existing bearer authentication:

- Authorization is `LoadoutPolicy#fork?` (signed in and able to view the
  origin); failures are the uniform 404.
- The optional body `{loadout: {slug, name, description}}` overrides the
  fork's attributes; each defaults to the origin's value.
- The controller is a thin wrapper over the existing `Loadouts::Fork`
  service. A slug collision returns the 422 `loadout_invalid` envelope
  with details; an origin without a published revision returns 422 with
  code `fork_invalid`.
- The response is 201 with the same loadout JSON the create endpoint
  returns. The operation is added to the OpenAPI document and its drift
  test.

No other server change. Fork republishes the origin's manifest through
`PublishRevision`, and its hosted-content validation already passes for
forkers because the download policy accepts any hash referenced by a
loadout the user can view.

## Install command behavior

- The plan listing shows hosted and github entries together. A github
  entry's line shows its repo and ref. Unfetchable entries (unparseable
  source, unknown source type) are listed with the reason.
- Per-entry failures (fetch error, locate ambiguity, declined or
  non-interactive remediation) are reported and counted; the command
  continues with the remaining entries. An entry installed through
  remediation counts as installed. The exit code is 1 only when at least
  one entry failed and nothing was installed or already present;
  otherwise 0.
- Collision handling is unchanged: identical content is "already
  installed", differing content requires `--force`, writes are atomic.
- Target resolution (shared store default, `--harness`, scope rules) is
  unchanged.

## Testing

- Unit: repo/ref parsing cases, top-level strip, locate by `skill_path`,
  locate by hash search with zero, one, and several SKILL.md candidates,
  each verification outcome, symlinked and unsafe tarball members
  rejected, the streaming size cap.
- Command: a local HTTP stub stands in for codeload.github.com (the
  fetcher takes an overridable base URL for exactly this). Tests cover a
  mixed revision (hosted plus github plus unparseable), the legacy
  SKILL.md-only path with its caveat, and the exit codes.
- Remediation: the owner path (review shown, publish on confirm, decline
  publishes nothing), the fork path (fork called, slug collision reprompt,
  then publish against the fork), and non-interactive runs skipping
  remediation with the hint.
- Server (drskill-web): request tests for the fork endpoint covering
  authorization, attribute defaults and overrides, slug collision, the
  no-revision error, forking a loadout with hosted entries, and OpenAPI
  drift.
- Manifest build: metadata recording for lockfile-governed, frontmatter,
  and unreadable-directory cases.
- No live GitHub in the suite. One manual live verification against a
  real repository before merge.

## Risks

- The ref may be a moving branch when nothing pinned exists. The
  directory hash still guarantees exactness at install time; the risk is
  a failed install after upstream moves, not a wrong install.
- `tree_sha` is recorded but not used for fetching, because codeload
  serves commit-ish refs, not tree objects. It stays in metadata for a
  future fetcher that can use the Git data API.
- GitHub tarballs for very large repos can be slow even under the size
  cap. Accepted; the cap bounds the damage and the message names the
  repo.
