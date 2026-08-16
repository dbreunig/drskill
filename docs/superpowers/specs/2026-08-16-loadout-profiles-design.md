# Loadout profiles and apply design

Date: 2026-08-16
Status: draft for discussion

## What this feature does

Everything drskill ships today is diagnosis: `scan` reads the loadout,
`audit` reads how it was used, `lint` reads what you publish. This design
adds the first treatment verb. It gives the user a way to say what each
project's loadout *should* be — as a named profile — and a command that
makes the filesystem match.

The two motions it exists for:

- "This global skill is on in a project where it is useless. Turn it off
  here, and only here."
- "New project. Give it my writing loadout: these five skills, nothing
  else."

Neither motion has a home today. Installers (`npx skills`, `gh skill`,
the store-and-symlink CLIs) are additive: they answer "how do I get a
skill into a project," and none of them model subtraction, presets, or
harness precedence. The harnesses themselves offer no per-project
off-switch for a user-scope skill — Claude Code alone has five open
feature requests asking for one. And a loadout manager is only as good
as its knowledge of each harness's discovery and precedence rules, which
is exactly the knowledge drskill has already built and verified
harness by harness.

## The trust boundary

The README promises: "drskill reads your files and never installs,
edits, or deletes a skill." That promise is load-bearing and this design
does not soften it — it fences it.

- `scan`, `audit`, `lint`, `list`, `show` stay read-only forever.
- The mutating verbs are new, separate commands: `apply`, `adopt`,
  `get`, `unapply`. They are never run implicitly by a read-only
  command, and nothing in a scanned repo can trigger them.
- The mutating verbs touch only two kinds of path: the store they own
  (`~/.agents/skills/store/`) and links or copies **they themselves
  created**, as recorded in a manifest. `apply` never edits a skill
  file's contents, and it never deletes a path that is not in its
  manifest. If a path it expected to own has been replaced by something
  it did not create, it stops and reports instead of overwriting —
  the same posture `ack` takes with the ledger.

The README's sentence becomes: "`scan`, `audit`, and `lint` read your
files and never install, edit, or delete a skill. `apply` writes, and
writes only what it owns."

## The store

Per-project subtraction is impossible while a skill lives loose in a
harness's global directory, because every project on the machine reads
that directory. So the first move is to give skills one canonical home
that no harness reads directly:

```
~/.agents/skills/store/
  <suite-or-solo>/<name>/
    SKILL.md                 # the skill and its bundled files, unmodified
    ...
  <suite-or-solo>/<name>.meta.toml
```

`meta.toml` records provenance: where the skill came from (a pinned git
source, a `gh skill` install, an adoption from a harness directory), its
content hash at install time, and the date. The store reuses the
`.agents/skills` location that the `linked` provenance kind already
recognizes, so a store-managed skill shows up in `list` today with no
new code.

Two verbs populate it:

- `drskill adopt` migrates existing skills in. It walks the same
  discovery paths `scan` walks, shows what it found in each harness's
  global and project directories, and — per skill, with confirmation —
  moves the skill into the store and leaves a symlink where it was.
  Behavior is unchanged after adoption (the harness follows the link);
  what changes is that the skill now has one home and can be linked
  into or omitted from any loadout. `adopt` is the only verb that
  touches a path it did not create, which is why it is interactive and
  per-skill, never bulk-silent.
- `drskill get <source>` fetches a new skill into the store: a
  `owner/repo/path@sha` git reference, a local path, or a marketplace
  reference. It pins what it fetched in `meta.toml`. It writes only to
  the store — linking it into a loadout is `apply`'s job. Content
  fetched by `get` immediately gets the standard content checks (spec,
  injection, budget), so a hostile skill is flagged before it is ever
  materialized anywhere a harness reads.

## Profiles

Profiles are machine-level configuration, so they live in
`~/.drskill.toml` next to the machine acks. A profile is a named set of
skill references with single inheritance and subtraction:

```toml
[profile.base]
skills = ["plain-writing", "code-review@superpowers"]

[profile.writing]
inherit = "base"
skills = ["docx", "elements-of-style-review"]

[profile.geo]
inherit = "base"
skills = ["overturemaps"]
remove = ["plain-writing"]
```

A skill reference is a store name, `name@suite` when two suites ship the
same name. Resolution is: start from the inherited profile's resolved
set, add `skills`, subtract `remove`. Inheritance is a single parent —
profiles are presets, not a type system, and diamond-merge questions are
not worth their complexity. A reference that names nothing in the store
is an error at `apply` time, and the error names the `drskill get`
command that would fix it, in the same fix-command style every finding
already uses.

The project side lives in the project's `drskill.toml`, next to the
project's acks, and is committed like them:

```toml
[loadout]
profile = "writing"
add = ["pptx"]
remove = ["docx"]
harnesses = ["claude-code", "codex"]   # optional; default: all detected
```

The committed file states intent (`profile`/`add`/`remove`); the store
supplies content. A teammate who clones the repo and runs `drskill
apply` gets told which store entries they are missing and how to `get`
them — the lockfile section below covers reproducing exact content.

Both files are parsed by the existing `ledger.Config` loader; new
tables are additive, and pydantic's extra-field tolerance means older
drskill versions ignore them rather than erroring.

## Materialization

`drskill apply` computes the effective skill set for the current
project — profile, plus `add`, minus `remove` — and makes each detected
harness's directories match it. Per harness:

- The write target is the harness's first project search path
  (`HarnessDef.project_paths[0]`), the same path precedence resolution
  already ranks highest. Each skill materializes as a directory symlink
  `<target>/<name> -> ~/.agents/skills/store/<suite>/<name>`.
- Harnesses verified to follow directory symlinks get links. A harness
  not yet verified for symlink-following gets a copy instead, plus a
  store-hash record so the existing lockfile-drift machinery can flag a
  copy that has diverged from its store source. Symlink behavior
  becomes a third per-harness verification axis in `harnesses.toml`
  (`symlink_verified`), alongside paths and precedence, verified
  empirically the way Copilot's rules already were.
- Everything `apply` creates is recorded in a manifest,
  `.drskill/applied.json` (committed: it holds names and relative
  paths, no machine paths). `apply` is idempotent — it adds missing
  links, removes manifest-listed links that the effective set no longer
  contains, and touches nothing else. `unapply` removes exactly the
  manifest's entries and nothing else.

Before writing anything, `apply` runs the same resolution pipeline
`scan` uses on the *proposed* state. If materializing would create a
shadow, a double-load, or a name collision in any target harness, apply
refuses and prints the finding, with the store commands that would
resolve it. The doctor gates the pharmacist: no state drskill writes
can be state drskill would then warn about.

### The default loadout

`drskill apply --default <profile>` materializes a profile into each
harness's *global* directories, for sessions started outside any
project. It uses the same manifest mechanism (manifest at
`~/.drskill/state/applied-global.json`) and the same idempotent sync.
This is what replaces "loose skills in `~/.claude/skills/`" after
adoption: the global directory becomes an output of `apply`, not a
place skills live.

## Subtraction and precedence

Subtraction is the hard part, and it is hard for exactly the reason
drskill exists: the harnesses disagree about precedence.

A skill materialized into the default (global) loadout is read by every
project on the machine. A project-level `remove` of such a skill cannot
delete it — other projects need it — so there are only two mechanisms,
and neither works everywhere:

**Hollow mode (the recommended model).** Keep the default loadout
minimal — ideally empty — and materialize every project's loadout
explicitly into project paths. Subtraction is then trivial: `remove`
means the link is simply not created. This works on **every** harness,
including the ones with unverified or unstable precedence, because it
never relies on precedence at all: there is no global copy to out-rank.
The cost is a per-project step, `drskill apply`, on the first visit to
a project — the direnv trade, and like direnv it is one command and
then it is done. `drskill new --profile geo` folds that step into
project creation.

**Tombstone shadowing (the narrow fallback).** When the user insists on
a rich default loadout, a project-level `remove` of a default-loadout
skill can only work by out-ranking the global copy. `apply` writes a
tombstone: a project-scope skill directory with the removed skill's
name and a generated minimal `SKILL.md` whose description marks it
disabled. The harness's own precedence then hides the global copy.
This is only sound where drskill has *verified* project-first
precedence (`search_order = "project-first"`, `precedence_verified =
true`). On Codex, whose `search_order` is `"none"` — every same-name
copy stays visible — a tombstone adds noise without subtracting
anything. On OpenCode, precedence is a verified coin flip. In both
cases `apply` refuses the remove, says exactly why in one line, and
names hollow mode as the fix. A tombstone still costs its catalog
tokens and still appears in `list` (flagged as a tombstone), which is
one more reason it is the fallback, not the model. Tombstones that
`apply` writes are fingerprint-acked automatically so `scan` does not
report its own output as a name-shadow finding.

The honest summary the docs should carry: per-project subtraction is
cheap when the loadout is per-project, and expensive when it fights a
global directory. drskill makes the cheap path convenient and the
expensive path explicit, per harness, with the same `?`-style honesty
the shadowing checks already use.

## Lockfile interop

`apply` maintains the project's `skills-lock.json` — the format `npx
skills` writes and drskill already parses — recording each materialized
skill's source and content hash. This keeps drskill a citizen of the
existing ecosystem rather than a competitor to it: a teammate can
restore content with `npx skills install` even without drskill, and
drskill's existing lockfile-drift check now guards store-copy
divergence for free. The hash-calibration caveat from the README
applies unchanged.

## Evidence-driven suggestions

This is the part no other manager can do, because no other manager has
`audit`. A new advisory mode, `drskill apply --suggest`, joins three
things drskill already computes — the effective loadout, per-skill
token cost, and per-project usage counts — and proposes edits to the
`[loadout]` block:

- "docx: 0 invocations in 14 sessions in this repo, 800 catalog
  tokens. Suggest `remove`."
- "overturemaps: invoked in 3 of your last 4 geo projects but absent
  here. Suggest `add`."

Suggestions print as diffs to `drskill.toml` and are never auto-applied.
Like audit itself, this reads traces and writes nothing.

## New commands

| command | writes | what it does |
|---|---|---|
| `drskill adopt` | store + harness dirs (interactive, per skill) | migrate existing skills into the store, leaving links |
| `drskill get <ref>` | store only | fetch and pin a skill into the store, run content checks |
| `drskill apply [--default] [--suggest] [--dry-run]` | manifest-tracked links/copies | make the filesystem match the effective loadout |
| `drskill use +name -name` | project `drskill.toml`, then apply | one-line edit of the `[loadout]` block plus an apply |
| `drskill new <dir> --profile <p>` | new project dir | scaffold a project with a loadout and apply it |
| `drskill unapply` | manifest-tracked paths only | remove everything apply created for this project |

Every command supports `--dry-run`, and `apply --dry-run` output is the
scan-style report of what would change, so the first run is always a
preview.

## Packaging

The verbs live in the standard `drskill` package but not in
`drskill-core`. CI has no business materializing loadouts, and keeping
the mutating surface out of the minimal install keeps the "safe to run
anywhere" property of core trivially true.

## Out of scope for v1

- MCP server profiles. The same `[loadout]` model extends naturally to
  generating `.mcp.json` from a profile, and it is the obvious v2, but
  MCP config generation touches secrets handling and per-harness config
  formats and deserves its own design.
- Store versioning beyond a single pinned copy per skill. `get`
  re-fetching a new sha replaces the copy and re-runs content checks;
  side-by-side versions wait for a real need.
- Any hosted/registry integration. Profiles-as-shareable-artifacts
  builds on the lint/registry service direction and stacks cleanly on
  top of this design, but nothing here depends on it.

## Open questions

1. Should `adopt` offer a bulk mode after the first few confirmations
   ("adopt the remaining 12 from this directory?"), or stay strictly
   per-skill? Bulk is friendlier; per-skill is the safer default for a
   verb that moves files it did not create.
2. Tombstone contents: an empty-description tombstone risks tripping
   `spec-missing-description` in other tools; a described one burns
   more catalog tokens. A one-line fixed description ("disabled here by
   drskill loadout") is the current lean.
3. Does `[loadout]` belong in `drskill.toml` or in a separate committed
   file? The ledger is append-only by convention (`ack` appends);
   `use` would need to *edit* the `[loadout]` table in place, which
   breaks the never-rewrite rule for that one table. A separate
   `loadout.toml` keeps the ledger append-only at the cost of a second
   file.
4. Claude Code plugin skills are store-delivered and namespaced
   (`plugin:skill`); they cannot be symlinked into a skills dir. v1
   treats plugins as outside `apply`'s reach (scan still covers them),
   but a later version could drive `enabledPlugins` per project the
   moment Claude Code supports it — the feature requests suggest it is
   coming.
