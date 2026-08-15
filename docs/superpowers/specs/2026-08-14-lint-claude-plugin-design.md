# Lint: Claude Code plugin layout + marketplace descriptors — Design

**Date:** 2026-08-14
**Status:** Approved (interactive brainstorming session); shipped 2026-08-15

## Problem

`drskill lint` deliberately scoped out Claude Code's `.claude-plugin`
layout in 0.6.5 (spec-core only). The ground has shifted since: the
ecosystem's emerging generator (prime-radiant-inc/everyharness) emits
`.claude-plugin/plugin.json` + `marketplace.json` as its primary Claude
Code output, and drskill's own scanner now reads installed plugins in
exactly that shape (plugin-stores cycle). Authors writing the most
common plugin format get no author-side checks, and marketplace
descriptors — which can point at unpinned remote sources or even run
shell commands at install time — are invisible to lint entirely.

Final everyharness-review follow-up (see
`2026-08-13-harness-empirical-verification.md` §Follow-ups).

## Decisions (settled during brainstorming)

- **Remote-source severity is pin-based**, mirroring mcp-unpinned:
  warning for the rug-pullable cases (no pin at all, insecure http://),
  note for partially pinned (ref without sha), nothing for fully pinned.
- **Schema facts are encoded as constants**, not vendored JSON Schema +
  a jsonschema dependency: matches plugin_spec.py's existing style, no
  new runtime dep, evidence-carrying messages. Facts cited to
  SchemaStore's generated claude-code-plugin-manifest.json (2026-04-23)
  and code.claude.com/docs (plugins-reference.md,
  plugin-marketplaces.md, retrieved 2026-08-14).
- **Dual-manifest repos** (root `plugin.json` AND
  `.claude-plugin/plugin.json`, e.g. everyharness output) classify as
  agent-plugins primary, additionally run the claude-code manifest
  checks, and get a cross-manifest consistency check.
- **Out of scope**: `strict: false` component-conflict detection (needs
  fetching the remote plugin — violates read-only-local); linting
  remote sources' contents; multi-plugin marketplace-repo generation
  concerns.

## Verified format facts (2026-08-14)

From code.claude.com/docs (plugins-reference.md, plugin-marketplaces.md)
and SchemaStore claude-code-plugin-manifest.json (generated 2026-04-23):

### plugin.json (.claude-plugin/plugin.json)

- Only required field: `name` (kebab-case, lowercase, no spaces). The
  manifest may be absent entirely (auto-discovery).
- Component-pointer fields: `skills`/`commands`/`agents`/`workflows`/
  `outputStyles` take string|array; `hooks`/`mcpServers`/`lspServers`
  take string|array|inline-object. `skills` ADDS to the default
  `skills/` scan; the others REPLACE their defaults.
- Default component locations when pointers are absent: `skills/`,
  `commands/`, `agents/`, `workflows/`, `hooks/hooks.json`, `.mcp.json`,
  `output-styles/`, `.lsp.json`, `themes/`, root `SKILL.md`
  (single-skill form, v2.1.142+).
- Known fields (union of schema + docs): $schema, name, version,
  displayName, description, author, homepage, repository, license,
  keywords, metadata, defaultEnabled, userConfig, channels,
  dependencies, skills, commands, agents, workflows, hooks, mcpServers,
  lspServers, outputStyles, themes, monitors, settings, experimental.

### marketplace.json (.claude-plugin/marketplace.json)

- Required: `name` (kebab-case), `owner` (object, `name` required),
  `plugins` (array). Optional top-level: `$schema`, `description`,
  `version`, `metadata.pluginRoot`,
  `allowCrossMarketplaceDependenciesOn`, `renames`.
- Entry required: `name` (kebab-case), `source`. Seven source forms:
  1. relative path string `"./..."` (resolved against marketplace root,
     honoring `metadata.pluginRoot`)
  2. `{source: "github", repo, ref?, sha?}`
  3. `{source: "url", url, ref?, sha?}`
  4. `{source: "git-subdir", url, path, ref?, sha?}`
  5. `{source: "npm", package, version?, registry?}`
  6. `{source: "archive", url, sha256?}` (v2.1.224+)
  7. `{source: "command", command, timeout?, mode?}` (v2.1.229+) —
     RUNS A SHELL COMMAND at install.
- Pinning: git forms `sha` (40-hex, precedence) / `ref` (branch or
  tag — movable); npm `version`; archive `sha256`.
- Optional entry fields: displayName, description, version, author,
  homepage, repository, license, keywords, category, tags, metadata,
  defaultEnabled, component overrides (skills/commands/agents/hooks/
  mcpServers/lspServers), `strict` (default true), `relevance`.

## Design

### Classification (lint.py)

`LintTarget` gains `plugin_flavor: Literal["agent-plugins",
"claude-code"] | None` (None on non-plugin kinds) and the `kind`
Literal gains `"marketplace"`. Routing in `classify`:

- dir with root `plugin.json` → plugin/agent-plugins (unchanged); if
  `.claude-plugin/plugin.json` also exists, a `dual_manifest: bool`
  flag is set on the target.
- dir with `.claude-plugin/plugin.json` (no root manifest) →
  plugin/claude-code.
- dir with only `.claude-plugin/marketplace.json` → kind marketplace.
- file named `marketplace.json` → kind marketplace.
- `--kind` force option accepts `marketplace`; forcing `plugin` on a
  `.claude-plugin`-only dir works and yields the claude-code flavor.
- A dir with none of plugin.json / .claude-plugin/* / SKILL.md keeps
  today's usage error, with `.claude-plugin` added to the accepted-
  targets message.

### checks/claude_plugin.py

Module constants encode the format facts above (with the dated
citations in a header comment). Checks, fingerprints path-free in the
mcp_spec style (`check_id|payload-hash|reason-slug`):

- `cc-manifest-invalid` (error): unparseable JSON, non-object, missing
  or empty `name`, name not kebab-case
  (`^[a-z0-9]+(-[a-z0-9]+)*$`), component-pointer field of the wrong
  type per the table above.
- `cc-manifest-unknown-field` (warning): top-level field outside the
  known-fields union; message lists the known set.
- `cc-component-missing` (error): a DECLARED pointer path (string or
  array element that is a path, not an inline object) that does not
  exist on disk, resolved against the plugin root. Default locations
  are optional and never flagged.
- `cc-manifest-mismatch` (warning, dual-manifest targets only): root
  and `.claude-plugin` manifests disagree on `name` or `version`
  (fields present in both and unequal; a field absent on one side is
  not a mismatch).

### checks/marketplace.py

- `marketplace-invalid` (error): missing/mis-typed `name`, `owner`
  (or `owner.name`), `plugins`; entry missing `name` or `source`;
  non-kebab-case marketplace or entry name; a source object missing
  its form's required field (github→repo, url→url,
  git-subdir→url+path, npm→package, archive→url, command→command).
  An UNRECOGNIZED source type string is a WARNING (reason
  `unknown-source-type`), not an error — Claude Code added two forms
  in recent releases; lint must not scream at formats newer than its
  fact base.
- `marketplace-unpinned-source`: WARNING for a git-form source with
  neither `sha` nor `ref`, npm without `version`, archive without
  `sha256`, and any `http://` (non-TLS) URL in a source (reason
  `insecure-url`); NOTE for a git-form source with `ref` but no `sha`
  (refs can be movable branches). Fully pinned sources (sha / npm
  version / sha256) produce nothing.
- `marketplace-command-source` (warning, always): the entry runs an
  arbitrary shell command at install; message quotes the command
  (escaped, one-lined). Same stance as Tier-3's on curl-pipe
  installers: legitimate uses ack it.
- `marketplace-entry-missing` (error): a relative-path source whose
  resolved directory does not exist (marketplace root +
  `metadata.pluginRoot` honored).

Local relative-path entries produce no pinning findings (nothing to
pin).

### Wiring

- `checks_for`: plugin/claude-code → `SKILL_CONTENT_CHECKS` +
  exact/near-duplicate + `CC_PLUGIN_CHECKS` + `MARKETPLACE_CHECKS`
  (no-op without a marketplace.json) + `MCP_SPEC_CHECKS`/
  `MCP_STATIC_CHECKS` when the manifest declares `mcpServers` or a
  default `.mcp.json` exists; plugin/agent-plugins with
  `dual_manifest` → today's suite + `CC_PLUGIN_CHECKS` (which includes
  the mismatch check) + `MARKETPLACE_CHECKS`; marketplace →
  `MARKETPLACE_CHECKS` only.
- `build_lint_world` collects skills for claude-code plugins from the
  default `skills/` dir plus declared `skills` pointers (and root
  `SKILL.md` single-skill form), so content checks and duplicates work
  identically to the agent-plugins flavor. MCP entries load from
  inline `mcpServers` objects or the pointed/default file using the
  existing mcp machinery.
- `render_lint`, exit codes, `--fail-on` semantics unchanged. Usage
  string and README lint section updated to name the new targets.

### Testing

- Classifier tests: the four new routing cases + forced-kind behavior +
  unchanged usage error.
- Per-check unit tests with fixture trees: valid claude-code plugin
  (zero findings); each cc-manifest-invalid shape; unknown field;
  declared-pointer missing vs absent defaults (no finding); dual
  manifest matched and drifted; marketplace fixtures covering all
  seven source forms pinned and unpinned, ref-no-sha note, http URL,
  command source, unknown source type, missing relative entry,
  pluginRoot resolution.
- Golden-style test for a marketplace repo (test_lint_golden.py
  pattern).
- Live gate (read-only): lint
  `~/.claude/plugins/cache/claude-plugins-official/superpowers/<active>`
  (real claude-code plugin), the everyharness kitchen-sink fixture
  (dual-manifest), and a local marketplace descriptor; read every
  finding once, expect signal not noise.

## Gate results (2026-08-15)

Full suite: `uv run pytest -q` — 698 passed, 0 failures.

Live gate (read-only, no acks, no writes to any target):

- **Real installed plugin** —
  `~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0`
  (14 skills). `drskill lint` → "No findings." Clean, as expected —
  this plugin's skills already pass `drskill scan`.
- **everyharness kitchen-sink fixture** — present at
  `.../scratchpad/everyharness/fixtures/kitchen-sink`, but it is an
  everyharness-flavor fixture (`everyharness.yaml`, `agents/`,
  `commands/`, `hooks/hooks.json`, `skills/`) with no `plugin.json`
  anywhere (root or `.claude-plugin/`) — not a dual-manifest
  claude-code plugin layout. Skipped per brief step 3 (fixture found
  but doesn't match the dual-manifest shape the gate targets).
- **Marketplace descriptor 1** —
  `~/.claude/plugins/marketplaces/superpowers-marketplace/.claude-plugin/marketplace.json`
  (9 plugin entries, all `source: url`). 8 `[marketplace-unpinned-source]`
  warnings (no `sha`/`ref` — e.g. `superpowers`, `superpowers-chrome`,
  `elements-of-style`) and 1 `[marketplace-unpinned-source]` note
  (`superpowers-dev` pins only a movable `ref: dev`, no `sha`).
  Verdict: real signal — this marketplace's entries genuinely track
  default branches; not noise.
- **Marketplace descriptor 2** —
  `~/.claude/plugins/marketplaces/claude-plugins-official/.claude-plugin/marketplace.json`
  (286 plugin entries). "No findings." Verified by hand: 53 entries
  use a bare string `source` (local relative paths like
  `./plugins/agent-sdk-dev`, since this marketplace lives inside the
  same repo as its plugins) and are correctly excluded from the
  unpinned-source check (string sources are skipped —
  `checks/marketplace.py` `marketplace_unpinned`); the remaining
  entries carry both `sha` and `ref`/`repo` pins. Verdict: correct,
  not a false negative.
- **Known issue found (non-blocking):** marketplace lint targets get
  a wrong header line — `render_lint` in `report.py` branches only on
  `target.kind == "plugin"` / `"skill"`; a `"marketplace"` target
  falls through to the MCP-config `else` branch and prints
  `drskill lint — MCP config (harness flavor), 0 servers` instead of
  a marketplace-appropriate header. Confirmed on both marketplace
  descriptors above. The underlying findings are unaffected — check
  selection (`MARKETPLACE_CHECKS`) and finding content are correct in
  both cases, and no test asserts the marketplace header text. Cosmetic
  only; logged here as a follow-up rather than patched during the gate.

Overall verdict: no crash, no nonsense finding, no false-positive
flood on the real superpowers plugin — gate passes. One cosmetic,
non-blocking display bug found on marketplace targets (see above).

## Follow-ups (logged, not this cycle)

- ~~Marketplace lint header~~ FIXED post-gate (commit 7bcf111):
  `render_lint` gained a marketplace branch with name + entry count.
- `strict: false` conflict detection if a local-source plugin dir is
  present (both sides locally readable — feasible subset).
- Marketplace `renames` / cross-marketplace dependency validation.
- Schema-verbatim validation tier if a jsonschema dep ever becomes
  worth it.
- ~~Lint-aware ack path~~ LANDED 2026-08-15 (`ack --lint <target>`,
  commit a74666c): resolves refs against the target's lint findings
  and writes to the target's config-root drskill.toml. Also:
  marketplace fingerprints hash the whole file text, so any edit
  re-fires every ack in that file — entry-scoped hashing (serialize
  just the entry's source object) would make acks on large
  marketplaces less brittle. Deferred minor: pathological multi-KB
  entry names embed verbatim in finding messages (cap via one_line
  like command sources).
