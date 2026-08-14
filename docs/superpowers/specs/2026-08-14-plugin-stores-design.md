# Plugin-install stores as scan and suite surfaces — Design

**Date:** 2026-08-14
**Status:** Approved (interactive brainstorming session); shipped 2026-08-14

## Problem

Harnesses load skills from plugin/extension stores drskill never scans.
gemini-cli loads installed extensions' skills (source-verified below);
codex, copilot, and droid deliver skills through installed plugins;
Claude Code loads plugin skills straight from its versioned cache. A
skill arriving that way today is invisible to scan — unscanned injection
surface, absent from `list`, excluded from overlap/duplicate checks —
and `suites.py` only knows Claude Code's cache, so plugin-delivered
skills elsewhere have no suite attribution.

Sparked by the everyharness review (see
`2026-08-13-harness-empirical-verification.md`); this is the
"plugin-install caches" follow-up from that spec.

## Scope decisions (settled during brainstorming)

- **Verified-facts-first**: cover the five stores whose locations and
  state semantics were verified this cycle (facts below). droid is
  included with its load semantics honestly marked best-effort (no
  offline enumeration verb; its store layout and state files were
  observed empirically via a sandboxed install).
- **Config-injected roots are a follow-up**: opencode's
  `config.skills.paths` / `plugin` keys require per-harness config
  parsing — a different mechanism, deferred.
- **Approach**: store adapters module (approach A). Rejected: suites
  attribution without scanning (forfeits injection/overlap coverage);
  data-only glob paths in harnesses.toml (provably wrong — stale
  versions scanned as duplicates, disabled plugins included, no suite
  names).
- No new checks, commands, or flags this cycle. Existing checks apply
  to the new contributors unchanged.

## Verified store facts

All facts dated; sources cited per store. "Empirical" = sandboxed
install probes run 2026-08-14 on macOS (fixture marketplace + plugin,
sandboxed $HOME), method as in `scripts/verify-harness-discovery.sh`.

### claude-code (empirical, this machine, 2026-08-14)

- Cache: `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`
  with skills under `skills/`. **Stale version dirs are retained**
  alongside the active one.
- Active installs: `~/.claude/plugins/installed_plugins.json` — per
  `name@marketplace` key, a LIST of installs, each with `scope`
  ("user", or "local" with `projectPath`), `installPath` (the active
  versioned cache dir), `version`, `gitCommitSha`, timestamps. A
  project can pin a different version than user scope (observed live:
  superpowers 6.3.0 user, 4.3.1 pinned to one project).
- Enablement: `~/.claude/settings.json` `enabledPlugins` map keyed
  `name@marketplace` → bool.
- Namespacing: Claude Code invokes plugin skills as `plugin:skill`, so
  a plugin/native name collision is not a real load conflict.

### codex (source, openai/codex commit 5bc8da6, current main 2026-08-14)

- Cache: `$CODEX_HOME/plugins/cache/<marketplace>/<plugin>/<version>/`
  (core-plugins/src/store.rs:26-27,131-139); default version segment
  `"local"`. Mutable data in `plugins/data/`.
- Skills roots: manifest `paths.skills` list, defaulting to
  `<plugin_root>/skills` (core-plugins/src/loader.rs:1059-1087), plus
  `.codex-plugin/migrated-command-skills` for legacy plugins. Roots are
  APPENDED to skill discovery (ext/skills/src/host_roots.rs:58-62), not
  copied into native roots.
- Recursion by manifest flavor: legacy manifests recursive (depth ≤ 6);
  agent-plugins `plugin.json` roots shallow (direct children only) —
  loader.rs:1031-1033, 880-882.
- Enablement: `$CODEX_HOME/config.toml`
  `[plugins."<name>@<marketplace>"] enabled` (config/src/plugin_edit.rs:21-34);
  disabled plugins' skills never load (loader.rs:843-845).
- Active version: no lockfile — prefer the `"local"` dir, else highest
  by version compare (store.rs:168-189).
- Collisions: no-shadowing extends to plugin skills (dedupe by path
  only, host_merge.rs:232-233); consistent with the harness's
  `search_order = "none"`.
- Marketplaces: registered in config.toml `[marketplaces.<name>]`;
  non-local sources materialized under `$CODEX_HOME/.tmp/marketplaces/`.

### gemini-cli (source, google-gemini/gemini-cli commit c0d1924, 2026-08-14)

- Store: `~/.gemini/extensions/<name>/` (extensions/storage.ts:23-39);
  identity from `gemini-extension.json` (required `name`, `version`).
- Skills: fixed `skills/` subdir (extension-manager.ts:921-922), loaded
  **non-recursively** — glob exactly `SKILL.md` + `*/SKILL.md`
  (skillLoader.ts:127).
- Enablement: `~/.gemini/extensions/extension-enablement.json`,
  per-extension path-rule overrides (last matching rule wins, `!path`
  disables; enabled by default). Disabled extensions' skills do not
  reach the SkillManager (skillManager.ts:66).
- Precedence: built-in → extension → user → workspace, last-wins on
  name — extension skills are overridden by user and workspace skills.

### copilot (empirical, CLI 1.0.80 sandboxed install, 2026-08-14)

- Store: `~/.copilot/installed-plugins/<marketplace>/<plugin>/` — the
  whole plugin copied once (unversioned dirs; version lives in state
  and in the copied `.claude-plugin/plugin.json`), skills under
  `skills/`.
- State: `~/.copilot/config.json` — **JSONC: comment lines precede the
  JSON** — `installedPlugins[]` with name/marketplace/version/
  installed_at/cache_path; `~/.copilot/settings.json` has
  `extraKnownMarketplaces` and `enabledPlugins`.
- Precedence (probed, stable across repeated runs): project > personal
  > plugin; `copilot skill list` shows plugin skills as their own
  "Plugin skills" tier, one winner per name.
- Recursion within a plugin's skills dir: not probed; harness default
  (recursive) assumed with a comment.

### droid (empirical, @factory/cli 0.196.0 sandboxed install, 2026-08-14; load semantics best-effort)

- Store: `~/.factory/plugins/cache/<mkt>-<hash>/<plugin>-<hash>/<installId>/`
  with skills under `skills/`.
- State: one JSON per install under `~/.factory/plugins/installed_plugins/`
  (`schemaVersion: 1`, `pluginId: "name@marketplace"`, `entry.scope`,
  `entry.installPath`, `entry.version`); marketplaces under
  `plugins/known_marketplaces/`. `droid plugin list` shows Active
  entries with scope.
- droid has no skill-enumeration verb, so THAT these cached skills load
  (and at what rank) rests on everyharness's container checks — marked
  best-effort in code comments; easy to drop the adapter if this proves
  wrong.

## Design

### stores.py

```python
class InstalledPlugin(BaseModel):
    harness: str                      # harness id
    name: str
    marketplace: str | None           # None for gemini extensions
    version: str | None
    scope: Literal["user", "project"]
    project_path: Path | None         # set when scope == "project"
    skills_roots: list[Path]          # ACTIVE install's resolved roots
    enabled: bool
    recursive: bool                   # per-store recursion for discovery
    evidence: Path                    # the state file that proved this

def discover_plugins(harness_id, home, project_root) -> list[InstalledPlugin]
```

One adapter function per store, registered in a module-level dict keyed
by harness id; `discover_plugins` dispatches. Adapters read ONLY their
harness's state files and never glob for actives:

- **claude-code**: parse `installed_plugins.json`; keep installs whose
  scope is "user" or whose `projectPath` matches the scanned project
  root; cross-check `enabledPlugins` — an entry explicitly `false` is
  disabled, a MISSING key counts as enabled (installed implies usable;
  for a scanner, over-scanning is safer than missing surface); roots =
  `installPath/skills`.
- **codex**: root is `~/.codex` (the `$CODEX_HOME` env override is
  ignored, matching how drskill already reads `~/.codex/config.toml`
  for MCP); parse config.toml `[plugins]` for enablement; resolve the
  active version dir (prefer `local`, else highest); roots from the
  plugin manifest's `paths.skills` (default `skills/`) plus
  `.codex-plugin/migrated-command-skills` when present; `recursive` by
  manifest flavor (legacy true, agent-plugins false — a shallow
  approximation of depth-2, noted in a comment).
- **gemini-cli**: list `~/.gemini/extensions/*/gemini-extension.json`;
  evaluate `extension-enablement.json` path rules against the scanned
  project root (default enabled); roots = `<ext>/skills`;
  `recursive = False`.
- **copilot**: parse `config.json` (strip leading `//` comment lines
  before json.loads) `installedPlugins` + `settings.json`
  `enabledPlugins`; roots = store dir `skills/`; `recursive = True`.
- **droid**: parse `plugins/installed_plugins/*.json`; scope from
  `entry.scope`; roots = `entry.installPath/skills`; `recursive = True`
  (best-effort comment).

Malformed or unreadable state files: that store contributes nothing and
the paths go to `world.unreadable` (existing surface) — never a crash,
never a guess.

### Models

- `Provenance.kind` gains `"plugin"`; `source` is
  `"name@marketplace==version"`, dropping the `@marketplace` part when
  there is none (gemini) and the `==version` part when unknown.
- `Contributor.suite` set at discovery for plugin contributors (the
  plugin/extension name, matching how suites render today).

### Discovery and precedence

`discover(h, project_root, home, global_only)` calls
`stores.discover_plugins(h.id, home, project_root)` after walking
`h.search_paths(...)`, appending each ENABLED plugin's roots with the
`order` counter continuing — plugin/extension roots rank below every
native path, encoding the proven rank on gemini (extension < user <
workspace... native paths already order project before global in
drskill's earlier-wins convention, and plugin-last preserves "any
native beats plugin") and copilot (project > personal > plugin). codex
`search_order = "none"` already skips shadow marking, preserving its
no-shadowing semantics. Scope on the RawInstance comes from the plugin
record. `global_only` scans keep user-scope plugins and drop
project-scoped ones.

Disabled plugins are skipped entirely (their skills demonstrably do not
load). Stale cache versions are never visited. Per-record `recursive`
overrides the harness default for those roots.

**claude-code shadow exclusion**: because Claude Code namespaces plugin
skills (`plugin:skill`), plugin instances on claude-code are excluded
from shadow PAIRING in resolution (they neither shadow nor get marked
shadowed by native skills). They remain full contributors everywhere
else — injection, duplicates, overlap, deep judge, budget.

### Suites

`suites.assign_suites` keeps its content-hash registry for attributing
FLAT COPIES of store skills (its original job), and the registry keeps
walking ALL cached versions — stale ones included — since users copy
from old versions. Store-scanned contributors skip matching entirely:
their suite is pre-attributed by the adapter. MCP-tool suite naming is
untouched.

### CLI / report

No new commands or flags. Plugin contributors render as normal skill
rows in the unified `list` table with their suite (suites already read
as blocks); provenance renders as `plugin`; scan header counts include
them; finding evidence quotes store paths like any other path.
Acks/fingerprints unchanged.

### Testing

- Per-adapter unit tests over fixture store trees covering the traps:
  stale version dirs not scanned; disabled entries skipped; copilot
  JSONC header; codex prefer-local-else-highest and manifest-flavor
  recursion; claude-code per-scope install lists (project pin does not
  leak into other projects); malformed state → unreadable, no crash.
- Discovery integration test per harness: plugin contributors land
  with correct scope, order (after native paths), provenance, suite.
- Resolution test: claude-code plugin/native name pair produces no
  shadow marking; gemini native-over-extension shadow does.
- Real-machine gate before merge: superpowers 6.3.0 appears
  suite-attributed from the claude-code store; the plumb 4.3.1 pin
  stays confined to that project; opencode pyportal double-load
  finding unchanged.

## Follow-ups (logged, not this cycle)

From the final whole-branch review (2026-08-14, all shipped-as-is by
ruling): double-load's fix command suggests deleting inside managed
plugin caches — should say disable/uninstall instead when a deployment
is plugin-delivered; hostile installPath/cache_path values are followed
verbatim (a state file pointing at / walks the filesystem — consider
bounding roots to the store dir); README's plugin-store paragraph sits
under Known limitations and elides the claude-code shadow-pairing
carve-out; typing.Callable → collections.abc.Callable; duplicate
unreadable entries when one state file has multiple mis-typed fields
(one-line not-in guard in _claude_code/_copilot). Plan-defect note for
the record: the plan's Task-2 reference code was actually RIGHT to stay
silent on a config.toml with no [plugins] table (normal no-plugins
state) — the wrong-shape-surfacing ruling was initially over-applied
there and corrected in the final fix wave.

- opencode config-injected skill roots (`config.skills.paths`,
  `plugin` entries) — needs config parsing.
- Stale-cache / plugin-version drift checks (e.g. flag a project
  pinned far behind user scope).
- gemini workspace-level extensions dir (`<project>/.gemini/extensions`
  exists in Storage but its install/load path was not verified this
  cycle — NOT CONFIRMED, so left out).
- codex depth-2 vs shallow approximation: revisit if a real agent-plugin
  ships nested skill dirs.

## Gate results (2026-08-14)

Full suite: `uv run pytest -q` — 646 passed (baseline was 611 before this
feature; 35 new tests across Tasks 1–6), no failures.

Real-machine gate, run read-only against this machine:

- `drskill list` on the drskill repo shows all 14 superpowers skills for
  Claude Code with `source = plugin`, `suite = superpowers`, `scope = user`
  — matching the machine's 6.3.0 user-scope install
  (`~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0/`).
  No 4.3.1 material appears in this repo's scan.
- Scanning from `/Users/dbreunig/Development/plumb` instead surfaces both
  the project-scope 4.3.1 pin (from `installed_plugins.json`'s `local`
  entry keyed to that `projectPath`) and the user-scope 6.3.0 install as
  distinct contributors — confirming project-scope confinement holds in
  both directions and no stale-version leakage occurs either way.
- `drskill scan` completed with no crash; `drskill scan --json` exited 1
  (findings present, as expected — not a failure).
- The pre-existing `[e010] double-load` finding (opencode loading
  `pyportal` twice) is present and byte-for-byte unchanged from
  2026-08-13.
- New findings appeared now that plugin skills participate in checks,
  all judged real on inspection: `exact-duplicate`/`near-duplicate`
  between the claude-code 6.3.0 cache, the codex 6.2.0 cache, and flat
  `~/.agents/skills` copies of the same superpowers skills (three
  genuinely different copies drifting apart); `injection-credential-read`
  and `injection-egress` on codex-cached `template-creator` and
  `sites-building`; `spec-name-mismatch` on codex-cached `Presentations`/
  `Spreadsheets`. All evidence paths point at real files with real
  content, not scanner artifacts. Nothing was acked; user state (ledgers,
  `~/.drskill/state/`) was left untouched.
- No bugs found: no crash, no wrong scope attribution, no stale cache
  version scanned as if active.
