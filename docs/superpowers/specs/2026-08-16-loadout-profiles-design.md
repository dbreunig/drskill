# Synced loadouts, project installation, and sharing

Date: 2026-08-16
Status: draft for discussion

## Product goal

drskill should let someone carry their decisions and loadouts across machines,
switch loadouts when they change tasks, and share useful loadouts with other
people.

The core motions are:

- "I am writing documentation now. Start Claude with my writing loadout for
  this session."
- "This project uses my textbook loadout until I change it."
- "I cloned a project. Install the exact loadout its author committed."
- "A friend published a good textbook-writing loadout. Let me inspect it,
  try it, and add it to my profile without silently following future changes."
- "Carry my machine-level drskill acknowledgments to my other machines."

The organizing analogy is Bundler. A project declares intent in a visible
file, locks exact resolved content in a second visible file, and requires an
explicit install command before anything is materialized:

| Bundler | drskill |
|---|---|
| `Gemfile` | `drskill.toml` |
| `Gemfile.lock` | `drskill.lock` |
| `bundle install` | `drskill install` |
| `bundle update` | `drskill update` |
| `bundle add` | `drskill add` |
| `bundle remove` | `drskill remove` |

The service is personal loadout infrastructure first and a community sharing
layer second. Team policy and enforcement are later products.

## Design principles

### Git carries project intent

`drskill.toml` and `drskill.lock` live at the project root. Before they are
committed, a selection is local to that checkout. Once committed, Git carries
it to the user's other machines and to collaborators.

Cloning these files has no effect by itself. Like cloning a Rails application
with a `Gemfile`, the user must run `drskill install` before the declared
loadout is materialized.

### The service carries portable personal state

The service stores versioned loadout definitions, their public presentation,
and machine-level acknowledgments. It does not replace Git as the source of
truth for a project's selected loadout.

### Machine state stays on the machine

The default harness, installed content, approval receipts, ownership
manifests, credentials, and caches are machine-specific. They do not sync just
because the user signs in.

### Mutation is explicit

`scan`, `audit`, `lint`, `list`, `show`, and `diff` remain read-only. A scanned
repository can never cause installation or execution. Mutation occurs only
through verbs whose names promise it: `install`, `update`, `use`, `add`, and
`remove`. `try` creates temporary state and launches a child process, then
restores the prior state.

### Runtime revisions require approval

Every new resolved runtime revision requires approval on each machine. Changes
to website metadata do not. The rule is intentionally simple and predictable.

## Vocabulary

### Loadout

A named desired set of agent capabilities. A loadout may contain skills,
plugins or extensions, and MCP server definitions, plus harness mappings.

### Loadout revision

An immutable published version of a loadout. Its identity is a hash of resolved
runtime state, not its description, recommendations, stars, or other website
metadata.

### Project intent

The human-edited `[loadout]` table in `drskill.toml`: a base loadout plus
explicit additions and removals.

### Project lock

The generated `drskill.lock`: exact loadout revision, entries, sources,
versions, content hashes, and harness mappings required to reproduce the
resolved state.

### Recommendation

Owner-authored editorial metadata displayed on a loadout's web page.
Recommendations are never part of TOML, the lock, installation, drift checks,
or reproducibility. Accepting one turns it into an ordinary addition to the
user's own loadout or project.

## Sources of truth

There are three intentionally separate sources of truth:

1. **Project:** Git and the working tree carry `drskill.toml`, `drskill.lock`,
   and existing project acknowledgments.
2. **Service:** immutable loadout revisions, loadout metadata, stars,
   recommendations, and synced machine/global acknowledgments.
3. **Machine:** default harness, downloaded content, approvals, materialization
   manifests, caches, and credentials.

The service does not need a private project-binding database. If someone wants
a project selection on another machine, they commit it and use Git. If they do
not commit it, it remains local to that checkout even though the referenced
loadout is available from the service.

## Service data model

### User

- Stable account ID and public handle.
- Owns loadouts.
- Owns machine/global acknowledgments.
- May star other users' loadouts.

### Loadout

- Stable owner-qualified name such as `drew/textbook`.
- Description and visibility: `private`, `unlisted`, or `public`.
- Defaults to private; publication is explicit.
- Has an ordered history of immutable revisions.
- Has owner-authored recommendations.
- Receives stars from other users.

### Loadout revision

- Immutable runtime-state hash.
- Ordered entries and harness mappings.
- Each entry records kind, name, source reference, version or revision, and
  content hash when available.
- Source references name upstream content rather than republishing third-party
  bodies. Entries without a reproducible source are marked `local-only` and
  cannot be published as installable dependencies.

### Following and forking

Adding another person's loadout to a profile follows its stable name. Existing
projects remain pinned to the revision in `drskill.lock`; upstream changes do
not alter them automatically. `drskill update` is the explicit path to a newer
revision.

Forking creates an independent loadout with attribution to its origin. A fork
does not follow upstream changes.

### Recommendations and stars

Only the loadout owner may add recommendations. Other users may star the
loadout. A star is both a bookmark and a lightweight quality signal. v1 has no
comments, ratings, or community-edited recommendations.

## Files on a machine

All drskill-owned user state lives under one inspectable directory:

```text
~/.drskill/
  config.toml              # machine-specific preferences
  acks.toml                # machine/global acks; service-sync eligible
  store/                   # immutable downloaded content
  state/
    projects/              # approval receipts and ownership manifests
  cache/                   # disposable downloads and reports
```

The directory has one purpose but its contents are separated because they have
different sync and lifecycle rules:

| path | synced | recoverable |
|---|---|---|
| `config.toml` | no | no |
| `acks.toml` | yes | from the service |
| `store/` | no | by running `install` |
| `state/` | no | yes, but approval is required again |
| `cache/` | no | always |

`~/.drskill/store/` is deliberately outside every harness discovery root. It
is immutable and content-addressed so installing a newer version for one
project cannot silently change another project's symlinked content.

Credentials and tokens live in the operating system's credential store, never
in these files. The existing `~/.drskill.toml` remains readable and can be
migrated into `config.toml` and `acks.toml` without changing ack semantics.

The project contains only the visible intent and lock files:

```text
project/
  drskill.toml
  drskill.lock
```

Harness-specific links, copies, or enablement settings are local installation
output. A machine-local ownership manifest records exactly what drskill
created, its expected type, target or content hash, and the loadout revision
that owns it.

## Project intent

`drskill.toml` continues to hold project acknowledgments and gains one
human-editable table:

```toml
[loadout]
base = "friend/textbook"
add = ["drew/plain-writing"]
remove = ["friend/citation-style"]
```

The model intentionally permits one base plus additions and removals. It does
not merge several complete loadouts, avoiding ambiguous precedence and
collision rules. Someone who wants an independent composition can fork the
base loadout.

Commands that change `[loadout]` must preserve comments, formatting, and the
append-oriented ack entries elsewhere in `drskill.toml`.

Existing ack behavior does not change:

- Findings entirely involving global/user skills ack to the machine ledger and
  may sync through the service.
- Findings involving project skills ack to project `drskill.toml` and travel
  through Git if committed.
- A future `--personal` override may be useful, but it is not required for this
  design.

## Project lock

`drskill.lock` records the exact resolved state:

- Base loadout name and immutable revision hash.
- Every installed skill, plugin, extension, and MCP definition.
- Reproducible source coordinates and exact versions or revisions.
- Content hashes.
- Harness mappings and the adapter version that produced them.
- Explicit `local-only` or otherwise non-reproducible entries.

`drskill install` honors an existing lock. It never silently selects newer
versions. When no lock exists, it resolves `drskill.toml`, presents the plan,
and writes the lock only as part of an approved installation.

`drskill update` is the explicit operation that resolves a newer upstream
revision and changes the lock.

The format is drskill-owned. Compatibility with third-party lock formats such
as `skills-lock.json` may be emitted only for entries that those tools can
genuinely restore; drskill does not overwrite unrelated installer-owned
entries or claim universal interoperability.

## Command model

### Machine configuration

```console
$ drskill set harness claude
```

This writes the machine-specific default harness to
`~/.drskill/config.toml`. An explicit harness on another command overrides the
default for that invocation.

### Temporary use

```console
$ drskill try textbook -- claude
$ drskill try friend/textbook
```

`try`:

1. Resolves the named loadout without changing project files.
2. Shows the runtime diff and health/security findings.
3. Requires approval for an unapproved revision on this machine.
4. Creates temporary harness-specific state.
5. Launches the explicit or default harness as a child process.
6. Restores the prior state when the child exits.

A recovery manifest lets the next drskill invocation detect and clean up an
interrupted trial. `try` never converts temporary state into project intent
without a separate user command.

### Persistent project selection

```console
$ drskill use friend/textbook
```

`use` updates `[loadout]` in the current project's `drskill.toml`, resolves and
writes `drskill.lock`, then runs the same installation flow as `drskill
install`. The selection remains until changed. It is local to the checkout
until the user commits the two project files.

```console
$ drskill add drew/plain-writing
$ drskill remove friend/citation-style
```

`add` and `remove` edit the override lists, re-resolve, show the diff, and use
the installation approval flow.

### Reproducing a cloned project

```console
$ git clone <repo>
$ cd <repo>
$ drskill install
```

The checkout is inert until `install` runs. `install` reads the committed lock,
fetches missing immutable content, shows the exact plan and findings, requests
approval if needed, and materializes the project state. Repeating it with the
same approved revision is non-interactive and idempotent.

### Updating an upstream loadout

```console
$ drskill update
```

`update` reports the available upstream revision and its resolved runtime diff.
If accepted, it writes the new lock and runs installation. Every runtime change
requires renewed machine approval, whether it adds, removes, or changes an
entry.

Website-only changes such as descriptions, recommendations, and stars do not
change the runtime revision and do not invalidate approval.

### Read-only inspection

```console
$ drskill show friend/textbook
$ drskill diff
$ drskill scan
```

`show` can inspect local or remote loadouts and their health reports. `diff`
compares project intent, the lock, installed state, and available upstream
revision without changing any of them. Existing read-only commands remain
read-only.

## Approval and acknowledgments

Installation approval is distinct from a finding acknowledgment.

An approval receipt is machine-local and keyed by project identity plus the
resolved runtime revision. The approval view lists:

- Added, removed, and changed capabilities.
- Exact sources and content hashes.
- Harness-specific changes.
- MCP commands, network-facing configuration, and required environment-variable
  names, never secret values.
- Current drskill health and security findings.

A new runtime revision always requires a new approval. Each machine approves
independently.

Finding acknowledgments continue to match content-based fingerprints. When an
upstream update changes relevant content, an old fingerprint no longer
suppresses a newly produced finding. Updating a loadout therefore both requires
installation approval and naturally re-evaluates existing finding acks.

## Materialization boundary

This design specifies behavior, not one universal filesystem mechanism.
Harness adapters choose among verified native enablement, directory links, or
copies. Each adapter must empirically verify its discovery, precedence, and
symlink behavior before it may mutate that harness.

The following rules apply to every adapter:

- Plan before mutation.
- Stage changes and either commit the complete new state or restore the old
  state.
- Touch only paths recorded in the machine-local ownership manifest.
- Before replacing or removing a path, verify its expected type, target, and
  hash; stop if a user or another tool replaced it.
- Treat shared physical directories such as `.agents/skills` as one destination
  even when several harnesses consume them.
- Never use a harness discovery directory as the canonical store.
- Never update mutable store content behind existing project links.
- Never use tombstone skills to pretend a global skill was disabled.

Exact adapters, atomic update mechanics, source fetching, and crash recovery
belong in narrower implementation specs informed by empirical harness tests.

## Web experience

A loadout page shows:

- Owner, description, visibility, and revision history.
- Resolved capabilities and their sources.
- Reproducibility and drskill health report.
- Owner-authored recommendations.
- Star count and whether the current user starred it.
- Actions to inspect, try, follow, or fork the loadout.

Visibility modes are:

- **Private:** visible only to the owner and used for personal sync.
- **Unlisted:** available by link but absent from search and browsing.
- **Public:** searchable, browsable, and eligible for stars.

New loadouts default to private. No page action installs content merely by being
viewed. Trying or adding a loadout returns to the CLI approval boundary before
runtime state changes.

## Privacy boundary

Audit traces and verbatim user queries never sync by default. They are not
required for loadout installation, sharing, recommendations, or stars.

The service stores source references and hashes rather than third-party skill
bodies wherever possible. It never stores MCP secrets. Private loadout metadata
and global ack notes are private account data, not discovery content.

## Delivery sequence

This product spans several independently testable subsystems and should not be
implemented as one plan. The recommended sequence is:

1. Define and validate the loadout intent and lock formats; add read-only
   capture/show/diff behavior.
2. Consolidate machine state under `~/.drskill/` while preserving legacy config
   and existing ack routing.
3. Add service accounts, private versioned loadouts, and global-ack sync.
4. Implement immutable local storage and one empirically verified harness
   adapter behind `install`.
5. Add `use`, `add`, `remove`, and explicit `update` on top of the same resolver
   and installation transaction.
6. Add supervised temporary sessions through `try`.
7. Add unlisted/public pages, following, forks, owner recommendations, and
   stars.
8. Expand materialization to additional empirically verified harnesses.

Each step must leave existing read-only commands safe and useful without the
service.

## Out of scope for the first implementation

- Organization policy, shared team ledgers, or CI enforcement.
- Automatic updates or automatic installation on repository entry.
- Multiple-base or diamond loadout composition.
- Community comments, ratings, or user-authored recommendations on someone
  else's loadout.
- Syncing audit traces or verbatim queries.
- Syncing secrets or machine-specific harness preferences.
- Claiming reproducibility for local-only content.
- Universal harness mutation before empirical adapter verification.

## Questions for implementation specs

The product contract intentionally leaves these decisions to narrower specs:

1. The exact serialized schemas and compatibility/versioning rules for
   `drskill.toml`, `drskill.lock`, and service revisions.
2. Project identity for machine-local approval receipts, including repositories
   with no Git remote and multiple checkouts of the same repository.
3. The first harness adapter and its verified temporary and persistent
   materialization mechanisms.
4. The source-fetching protocol and which upstream source types qualify as
   reproducible in v1.
5. The authentication and merge protocol for syncing `acks.toml` without
   overwriting concurrent decisions.
