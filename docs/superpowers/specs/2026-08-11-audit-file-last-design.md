# Design: `--file` and `--last` selectors for `drskill audit`

Date: 2026-08-11
Status: approved

## Problem

The `audit` command always sweeps every trace file that each harness adapter
discovers. There is no way to audit one specific trace file, and no way to
audit only the most recent session. Today the closest workaround is to pass
`--harness` and `--since` together and then filter the JSON output by
`source_file` yourself.

## What we are adding

Two new options on `drskill audit`.

- `--file <path>` audits exactly one trace file.
- `--last` narrows the normal audit to the single most recent session.

The two options cannot be combined. Passing both is an error that exits with
code 1, which matches the error style the audit command already uses.

## Behavior of `--file`

The user names one trace file and the audit covers only that file.

- The harness adapter is inferred from the path. Each adapter has a known
  discovery root, e.g., a path under `~/.claude/projects/` maps to the
  claude-code adapter. If the path is under a known root, that adapter parses
  the file.
- `--harness` overrides the inference. If the path is outside every known root
  and `--harness` was not given, the command errors and asks for `--harness`.
- The file is extracted directly and is not written to the audit cache. The
  cache prunes entries for traces that discovery no longer finds, so caching a
  file outside the discovery roots would create an entry that is pruned on the
  next run. Skipping the cache avoids that, and extracting one file is fast.
- The project scope filter does not apply. When a user names a file, the file
  is the scope. The audit reports every invocation in it, even when the
  sessions belong to a different project.
- A missing or unreadable file is a hard error that exits with code 1. It is
  not added to the `unreadable` list in the report.
- `--since`, the positional `name` drilldown argument, and `--json` still
  apply to the invocations extracted from the file.

## Behavior of `--last`

The normal pipeline runs exactly as it does today. Discovery, the cache, and
the filters for project scope, `--global`, `--harness`, and `--since` all
apply first. After filtering, the result is narrowed to one session. The
command finds the invocation with the newest timestamp and keeps only the
invocations that share its `source_file`.

Narrowing after extraction has two benefits.

- The meaning is exact for every harness. For codex, pi, and copilot, project
  membership is only knowable after extraction, so picking the newest file by
  modification time before extraction could pick a file with no invocations in
  scope.
- The full sweep is cheap because the cache holds prior extractions.

When no invocations survive the filters, the report is empty, the same as
today.

## Code changes

- `run_audit()` in `src/drskill/traces/pipeline.py` gains a parameter for the last-session flag. A separate new function `run_audit_file()` handles audits of an explicit file.
- A small helper maps a path to an adapter by checking each adapter's
  discovery root.
- `src/drskill/cli.py` adds the two options, the mutual exclusion check, and
  the error messages.

## Tests

- Inference maps a path under each adapter's discovery root to the right
  adapter.
- A path outside every root without `--harness` errors.
- `--file` and `--last` together errors.
- `--last` keeps only the invocations from the newest session in scope.
- `--file` reports invocations from a file that belongs to another project.
- A missing `--file` path errors with exit code 1.
