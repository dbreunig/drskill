# Harness discovery — empirical verification round

**Date:** 2026-08-13
**Status:** Shipped (data + probe script + tests, no code changes)

## Motivation

Reviewing prime-radiant-inc/everyharness (an author-side generator that
emits native plugins for 13 harnesses and container-tests them with real
installs) surfaced a verification method drskill's harness table lacked:
several harness CLIs expose **offline skill-enumeration verbs** that can
prove discovery behavior empirically — `copilot skill list`,
`opencode debug skill`, `gemini skills list --all`,
`codex debug prompt-input`, `hermes skills list`. drskill's copilot entry
was unverified on both facets (closed-source CLI, docs silent on recursion
and precedence), and the vendored opencode entry was best-effort seed
data. This round used those verbs to upgrade what could be proven.

everyharness's own container image (~15GB, 17 harness CLIs) did not fit
the local Docker VM (61GB cap, 8.4GB free), so probes ran **locally on
macOS** against npm-fetched CLIs in a sandboxed prefix — arguably better
evidence for drskill anyway, since that is the platform drskill runs on
here.

## Method

`scripts/verify-harness-discovery.sh` (committed) builds a throwaway
fixture tree and runs each available CLI's enumeration verb under a
**sandboxed $HOME**:

- one uniquely-named skill per candidate discovery directory — presence
  in the enumeration proves the directory is read;
- skills nested two levels below roots — presence proves recursion;
- same-name collision pairs across scopes and across sibling project
  dirs, with distinguishable descriptions — the surviving copy proves
  precedence, and **repeated runs** distinguish stable precedence from
  nondeterministic dedupe.

Fixture descriptions must be colon-free: copilot's frontmatter parser
rejects a skill whose unquoted description contains `: `.

## Findings

### copilot (GitHub Copilot CLI 1.0.80) — both facets now verified

- Reads exactly the five seeded dirs: project `.github/skills`,
  `.agents/skills`, `.claude/skills`; personal `~/.copilot/skills`,
  `~/.agents/skills`. Does NOT read `./skills` or a project
  `.copilot/skills`.
- Recursive in both scopes.
- Collisions are deterministic across all runs (11+): exactly one entry
  survives; project shadows personal outright (unlike codex, where both
  stay visible). Within project scope `.github > .agents > .claude` —
  note GitHub's docs list `.claude` before `.agents`, but the CLI
  resolves the other way, so `project_paths` was **reordered**. Within
  personal scope `~/.copilot > ~/.agents`.
- Evidence caveat (recorded in the toml comment): precedence evidence is
  the CLI's own enumeration deduping; with a closed-source CLI that is
  the strongest observable signal.

### opencode (opencode-ai 1.18.18) — paths verified; precedence provably nondeterministic

- Reads EIGHT dirs: `.opencode/skills`, `.opencode/skill` (singular and
  plural both work, per its built-in customize-opencode skill),
  `.agents/skills`, `.claude/skills`, and the same four under `$HOME`
  (opencode-native ones under `~/.config/opencode/`). Project
  `.claude/skills` is read even though the built-in doc table only
  mentions the global one. Recursive.
- Does NOT read cwd `./skills` (an everyharness code comment implies it
  does; their plugin actually injects it via `config.skills.paths`) or
  `.github/skills`.
- **Collision dedupe is racy**: one entry survives per run, but across
  12 runs the winner flipped for 3 of 5 tested pairs (project-vs-global
  `.opencode` 6:4, `.opencode`-vs-`.agents` 8:2, `.agents`-vs-`.claude`
  10:1). Path order encodes the modal winners; `precedence_verified`
  stays false with the race documented. A same-name collision on
  opencode is effectively a coin flip — exactly the situation drskill's
  duplicate/shadowing findings exist to flag.

### hermes (Hermes Agent v0.20.0) — partial positive evidence only

`~/.hermes/skills` is read recursively (fixture skills list as source
"local"). Project `.hermes/skills` did not appear, but `hermes skills
list` provably omits some runtime-loaded skills (plugin-registered ones
never show, per everyharness's install checks), so absence is not
disproof. Entry stays best-effort; evidence noted in its comment.

### Not probeable offline (unchanged)

- **droid** (@factory/cli 0.196.0): no skill-enumeration verb (only
  `mcp`/`plugin` subcommands); everyharness verifies it by grepping
  droid's plugin cache (`~/.factory/plugins/cache`) on disk.
- **grok**: CLI not on npm; everyharness reaches it only through
  `grok plugin install` + a "skill dir" count in `plugin details`, which
  proves plugin-cache skills, not native discovery dirs — not enough to
  seed a drskill entry.
- **cursor / kimi / devin**: no offline enumeration path (everyharness
  reports them as `skip` too). cursor's precedence facet stays the one
  doc-verified-paths entry with an unverified precedence.

## Data changes

`src/drskill/data/harnesses.toml`: copilot both facets true +
reordered `project_paths`; opencode replaced (8 paths, detect gains
`.opencode`, paths_verified true, racy-precedence comment); hermes
evidence comment; vendored-section header now names its verified
exceptions. `tests/test_harnesses.py`: opencode joins the verified-core
allowlist; new `test_copilot_rules_match_probes` and
`test_opencode_rules_match_probes` pin the empirical results.

## Follow-ups (from the everyharness review, not this round)

- Plugin-install caches as scan/suite surfaces: codex
  (`.agents/plugins/marketplace.json`), gemini extensions (which load
  skills), droid (`~/.factory/plugins/cache`), copilot marketplaces.
- Lint: vendor Agent Plugins / Claude Code manifest JSON schemas as a
  base validation tier; reconsider recognizing `.claude-plugin` layout;
  marketplace descriptors with remote sources as a supply-chain check.
- Generated-provenance awareness: treat `.everyharness/manifest.json`
  as provenance so diverged-copies doesn't propose rm/ln fixes for
  intentional generated copies.
- Rerun the probe script when CLI versions bump; the opencode race is
  version-specific behavior worth rechecking.
