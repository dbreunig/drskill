# External source install design

Status: Approved (design approved in discussion 2026-09-02)
Date: 2026-09-02
Scope: drskill CLI only. Publish records fetch coordinates and a directory
hash for external entries; `drskill loadout install` fetches `github`
entries from GitHub, verifies them, and installs them alongside hosted
entries. The drskill-web service is unchanged.

## Goal

`drskill loadout install owner/slug` installs every installable entry in a
revision, not only the hosted ones. A `github` entry is fetched from the
upstream repository, verified against hashes recorded at publish, and
written through the same safe install path hosted content uses. Entries
published before this feature still install when their SKILL.md matches,
with a stated caveat.

## Out of scope

- Server changes. Entry `metadata` is already free-form, and the server
  validates nothing new.
- Escrow (uploading copies of external skills to the service). Considered
  and set aside for licensing reasons.
- Sources other than GitHub. `source_type` values besides `drskill` and
  `github` remain uninstallable and are reported as skipped.
- Lockfile writing. Locking belongs to the future resolution phase.

## Publish side: recorded metadata

`manifest_build.contributors_to_manifest` writes these keys into the
`metadata` object of every entry whose `source_type` is `github`:

- `directory_hash`: the manifest hash of the skill directory, computed
  from disk with `content.manifest_hash(content.collect_files(c))`. This
  is the same algorithm hosted content uses, so one identity verifies
  everything. Omitted when the files cannot be read.
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
4. Extract. Members under the skill directory pass the same gate as
   hosted downloads: regular files only, no symlinks or links or devices,
   safe relative paths, at most 200 files and 20 MB unpacked. The result
   is the same `[{path, data, executable}]` shape the rest of content.py
   uses.

## Verification policy

- An entry with `metadata.directory_hash` must match it exactly. A
  mismatch fails that entry with "upstream has changed since this revision
  was published", and nothing is written for it.
- An entry without `directory_hash` (published before this feature) is
  verified by its existing `content_hash` against the fetched SKILL.md
  text, normalized the way the scanner normalizes
  (`resolution.normalize_content` under `resolution.content_hash`). On a
  match the install proceeds and prints one caveat naming the entry:
  bundled files are unverified. On a mismatch the entry fails.
- There is no flag to skip verification.

## Install command behavior

- The plan listing shows hosted and github entries together. A github
  entry's line shows its repo and ref. Unfetchable entries (unparseable
  source, unknown source type) are listed with the reason.
- Per-entry failures (fetch error, locate ambiguity, verification
  mismatch) are reported and counted; the command continues with the
  remaining entries. The exit code is 1 only when at least one entry
  failed and nothing was installed or already present; otherwise 0.
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
  SKILL.md-only path with its caveat, a directory-hash mismatch, and the
  exit codes.
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
