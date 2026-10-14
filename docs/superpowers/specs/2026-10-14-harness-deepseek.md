# Harness discovery — DeepSeek Harness added

**Date:** 2026-10-14
**Status:** Data + tests, no code changes

## Motivation

DeepSeek Harness (DSH) is a plugin-composed agent harness with its own skill loader
(`@deepseek-ai/dsh-skill-filesystem`) and a second, independent home for its desktop
application. It was not in the table, so a DSH user's entire catalog — 59 skills over
four homes on the machine this was measured on — was invisible to every check.

Unlike the 2026-08-13 round there is no offline enumeration verb to lean on: `dsh`
boots a profile, `dsh plugin` shells out to pnpm, and `--dump-config` prints the
composition (which is where `customSkillDirs` was confirmed, see *Not covered*). The
engine itself had to be the probe.

## Method

A fixture tree with one uniquely-marked skill per candidate root, same-name collision
pairs carrying distinguishable descriptions, skills two and three levels below a root,
and one bare `*.md` file directly in a root. Booted with

```
DSH_HOME=<fixture>/home DSH_AGENTS_HOME=<fixture>/agents \
  dsh --profile headless --json "list every skill whose name starts with zzprobe …"
```

from the fixture project directory. `headless` answers one task and exits, and the
answer names what the catalog actually holds. The fixture home carried a copy of the
profile and read its packages through a junction to a live profile, so no real home was
written to. Five runs.

`DSH_AGENTS_HOME` is the isolation seam for the fourth root:
`config.agentsHome ?? process.env.DSH_AGENTS_HOME ?? join(homedir(), ".agents")`
(`lib/index.js:78`).

## Findings

- **Roots.** All four are read: project `.dsh/skills`, project `.agents/skills`,
  `$DSH_HOME/skills`, `$DSH_AGENTS_HOME/skills`.
- **Not recursive.** Marked skills two and three levels below a root never appeared in
  any run. `discoverRoot()` (`lib/index.js:581`) lists a root's immediate entries only:
  a directory becomes a skill through `<dir>/SKILL.md`, and the watcher agrees —
  `isPotentialSkillPath` (line 552) accepts relative depth ≤ 2. So `recursive = false`,
  the opposite of most entries in the table.
- **A bare `*.md` file directly in a root is a skill** (line 589) — hence
  `root_md_paths` for all four roots. No live root carries one today.
- **Precedence is deterministic and rank-ordered**, identical across all five runs:
  project `.dsh/skills` (rank 100) beat user `.dsh/skills` (400), project `.dsh/skills`
  beat project `.agents/skills` (200), user `.dsh/skills` beat user `.agents/skills`
  (500). No flipping — unlike opencode's race in the previous round, where the same
  fixture method had to leave `precedence_verified` false.
- **The desktop app is a second harness, not a second path.** It runs the same engine
  with its own `DSH_HOME` (`%APPDATA%\dsh-desktop\harness`), its own profile and its own
  sessions, and the two homes never load into one context — declaring them as one entry
  with two paths produced a false `double-load` for every shared skill. It also reads the
  machine's shared `~/.agents/skills`.

## Data changes

- `src/drskill/data/harnesses.toml`: `dsh` and `dsh-desktop-app` added — both facets
  verified, `recursive = false`, `root_md_paths` for every root, evidence in the entry
  comments.
- `tests/test_harnesses.py`: both ids join the verified-core allowlist;
  `test_dsh_rules_match_probes` and `test_dsh_detect_markers` pin the results.

## Not covered

The custom root — `customSkillDirs` in the composed config, an absolute path built from
`require.resolve('@deepseek-ai/dsh-agent-preset/package.json')` — and the bundled root.
The schema has no field for a configured absolute root, so those skills stay invisible to
drskill; a `custom_paths` field would close that gap.

The `user-dsh` root alone declares `skipSystem` (line 175), so a `.system` subdirectory
there is not a skill. No field expresses it and no live root carries one.
