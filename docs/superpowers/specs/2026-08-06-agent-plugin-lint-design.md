# Design: drskill lint for plugins, skills, and MCP configs

Date: 2026-08-06
Status: approved design, not yet implemented

## Goal

Add a `drskill lint` command for people who write plugins, skills, and MCP
configs. The command points at one directory or file, checks it against its
standard where one exists, runs drskill's existing quality and security checks
over its contents, and exits with a code that CI can act on. The first users
are authors who want to check their work before they publish it. A later
release can reuse the same checks to audit installed plugins.

The standard for plugins is the Agent Plugins specification, version 1.0.0,
published at agent-plugins.org. A plugin is a directory with a `plugin.json`
manifest, an optional `skills/` directory of Agent Skills, an optional
`mcp.json` file of MCP server configs, and optional client extension
directories named by reverse domain, e.g. `com.example.client/`.

## Command surface and target detection

`drskill lint [PATH]` defaults to the current directory. It classifies the
path as one of three target types.

- Plugin. The path is a directory that contains `plugin.json`. Lint runs the
  full stack. That is the spec conformance checks, the hygiene checks, and the
  content checks over every skill and MCP server the plugin ships.
- Skill. The path is a directory that contains `SKILL.md`, or the path is a
  `SKILL.md` file. Lint runs only the skill checks. Those are the SKILL.md
  spec check, frontmatter and description quality, token budget, injection
  heuristics, broken symlinks, and the shell command checks. Plugin checks do
  not run and do not appear as skipped noise.
- MCP config. The path is a JSON file whose content matches one of two
  flavors. Detection reads the content, not the filename.
  - The Agent Plugins flavor has a `$schema` field that points at
    `agent-plugins.org/schemas/<version>/mcp.schema.json`, or the file sits
    next to a `plugin.json`. This flavor gets the strict conformance checks.
  - The harness flavor is a bare `mcpServers` map, e.g. a `.mcp.json` file.
    There is no standard to enforce for this flavor, so it gets structural
    checks and the security checks, but no conformance findings.

If the path matches none of these, lint exits with a usage error and one line
that explains what it accepts. If a directory contains both `plugin.json` and
`SKILL.md`, the plugin classification wins. A `--type plugin|skill|mcp` flag
overrides detection for edge cases.

Flags shared by all target types:

- `--json` emits findings as JSON with stable finding ids, in the same shape
  as `scan --json`.
- `--fail-on error|warn` sets the severity that fails the build. The default
  is `error`.
- `--deep` opts into the model judged checks, the same as scan.
- `--mcp-connect` opts into the checks that need a live server connection,
  the same as scan.

Lint makes no LLM calls and no network connections unless the user passes
those flags.

Exit codes:

- 0 when there are no findings at or above the fail threshold.
- 1 when there are findings at or above the fail threshold.
- 2 on a usage error, e.g. the path is not a lintable target.

## Architecture

Lint reuses the scan engine. Scan already works in stages. It builds a
`World`, runs every check in a registry, collects `Finding` objects, and
renders a report. Lint replaces only the first stage.

- A new module `lint.py` holds target classification and a
  `build_lint_world(target)` function. For a plugin target it parses
  `plugin.json` into a new `PluginManifest` model, discovers child skills the
  way the spec says clients must, loads each skill through the existing skill
  parsing path into `Contributor` objects, and loads `mcp.json` into the
  existing `MCPServer` model. Skill discovery is not recursive. A skill is an
  immediate child directory of `skills/` whose `SKILL.md` resolves to a
  regular file. For a skill target the function builds a world with one
  contributor. For an MCP target it builds a world with only servers.
- `World` gains one optional field, `plugin: PluginManifest | None`.
- Lint contributors have no `Deployment` entries, so `Finding.harnesses` is
  empty. The report layer learns to render a finding without harness
  attribution. Today it assumes at least one harness.
- Each target type has an explicit list of check ids to run, named
  `LINT_PLUGIN_CHECKS`, `LINT_SKILL_CHECKS`, and `LINT_MCP_CHECKS`. A
  `run_checks(world, config, ids)` variant of `run_all` runs one list. Checks
  that only make sense in a scan, such as cross harness shadowing and
  lockfile drift, are simply not in the lists. No check needs to know that
  lint exists.
- Lint finds its config by walking up from the target path to the nearest
  directory that contains `drskill.toml`, so `drskill ack`
  works the same as it does for scan. An author can acknowledge a warning and
  CI goes green. Ack fingerprints hash the content the check judged, so an
  ack expires when that content changes.

## New checks

Two new check modules cover what is new. Everything else is reuse.

`checks/plugin_spec.py` checks `plugin.json` and the plugin layout.

- A manifest that fails to parse, fails the schema, or breaks the name rules
  is an error. The spec calls these violations fatal, and a client rejects
  the whole plugin.
- An unknown top level field, or an `extensions` field that is not an object,
  is a warning. The spec calls these violations non fatal.
- A missing or unknown `$schema` version is a warning. Lint validates against
  1.0.0 and says so in the finding.
- A skills discovery mismatch is a warning, e.g. a `SKILL.md` nested too deep
  for a client to find. The finding asks whether the author intended that.
- A symlink that resolves outside the plugin root is an error. The spec
  requires clients to reject paths that escape the root.
- Extension hygiene findings are warnings. These cover a namespace directory
  whose name is not a valid reverse domain name, a secret in an extension
  file, and an extension directory that shadows a portable component.

`checks/mcp_spec.py` checks the Agent Plugins flavor of `mcp.json`.

- Schema validation of the file and of each server entry.
- Transport rules. A stdio `command` must be a single token, either a bare
  name or a path that starts with `./`. URLs must be absolute, must not carry
  user info or fragments, and must not use plain HTTP except to loopback.
- Placeholder rules. `${PLUGIN_ROOT}` and `${PLUGIN_DATA}` may appear only in
  `args` elements, `env` values, and `cwd`. They may not appear in `env` keys
  or in `command`. A server `env` may not define an entry named `PLUGIN_ROOT`
  or `PLUGIN_DATA`.
- `cwd` form validation. Valid forms are a `./` relative path, or a path that
  starts with `${PLUGIN_ROOT}` or `${PLUGIN_DATA}`.

A standalone `mcp.json` linted without a surrounding plugin cannot fully
resolve `${PLUGIN_ROOT}`. Lint treats the file's parent directory as a
provisional plugin root for containment checks and states that assumption in
the finding text.

Reused without change: `spec`, `budget`, `injection`, `skill_shell`,
`filesystem`, `duplicates` scoped to within the plugin, and `mcp` for
secrets, unpinned packages, and missing commands. Behind `--mcp-connect`,
`mcp_tools` and `mcp_injection` also run.

## Data flow and error handling

The flow is short. Classify the path, build the lint world, run the check
list for the target type, render the report, exit with the code.

Error handling mirrors the failure boundaries in the spec. Nothing short of
an unreadable path crashes lint.

- A corrupt `plugin.json` produces an error finding, and lint still checks
  the skills and `mcp.json` beneath it as well as it can.
- An invalid child skill produces the warning that clients will skip it, and
  lint still runs the content checks on it so the author can fix it.
- A JSON parse error finding carries the line number.

## Testing

- Fixture plugins live under `tests/fixtures/plugins/`. There is one valid
  plugin, one plugin that packs in many violations, and a set of minimal
  fixtures with one violation each. Symlink escape fixtures are built at test
  time because a committed symlink does not travel well across platforms.
- Conformance tests are table driven. Each row maps a spec clause to an
  expected check id and severity. When the spec revs, the table shows what is
  covered.
- CLI tests cover target classification, exit codes, `--fail-on`, the shape
  of `--json` output, and the ack round trip, where a lint, an ack, and a
  second lint ends green.

## Out of scope for this release

- Auditing installed plugins during `drskill scan`. The check modules are
  written so that a later release can add this.
- Validating the private conventions of any client's extension namespace.
  Lint checks extension hygiene only.
- Linting a whole marketplace of plugins in one run.
