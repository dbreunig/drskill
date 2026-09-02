# Loadout wizard: live walk-through fix report

Fixes for the three spec gaps recorded in "Revisions after the live
walk-through (2026-08-31)" in
`docs/superpowers/specs/2026-08-31-loadout-wizard-design.md`. Branch
`loadout-wizard`. Commit: `a70bc77` "feat: harness step, dedup, and scan
spinner in the wizard".

## Note on branch state at the start of this fix

Before starting, `git log` showed two commits on `loadout-wizard` I had not
made in this session: `d3b1697 docs: revise wizard spec from live
walk-through feedback` and `c2924e1 feat: start the create wizard by default
in a terminal`. The latter reworked `create`'s flag surface — `--from-project`
was removed in favor of "interactive by default when stdin+stdout are a TTY"
plus a new `--empty` opt-out — while leaving `loadout_wizard.run(...)`'s
call signature unchanged. Both commits carry the same session-credential
footer pattern used for my own commits, so this reads as concurrent work by
another agent instance on the same branch rather than drift or corruption.
I re-read `src/drskill/cli.py`'s current `create` command and the full
current `tests/test_loadout_wizard.py` before touching anything, and built
the three fixes below against that current state (invoking
`["loadout", "create", "pack"]` without `--from-project`, matching how the
existing tests now call it). `src/drskill/loadout_wizard.py` itself was
untouched by that commit, so its contents matched what I already knew.

## 1 — TTY guard covers stdout too

Already correct going into this pass — `_stdin_is_tty()` in
`src/drskill/loadout_wizard.py` already checked
`sys.stdin.isatty() and sys.stdout.isatty()` (from the prior fix round). No
change needed here; verified by re-reading the function and confirming
`test_non_tty_falls_back_to_plain_create` and
`test_wizard_flags_error_when_wizard_cannot_run` (the current test file's
non-tty coverage) still pass.

## 2 — Scan spinner

Replaced the static `typer.echo("Scanning the current project...")` with
the same live-status pattern `drskill scan` uses in `cli.py` (`with
console.status(...) as status: ... progress=lambda m: status.update(...)`).

**Circular import.** `cli.py` imports `loadout_wizard` lazily inside
`create()`'s body, not at module top level, specifically so `loadout_wizard`
can be imported standalone (as tests do: `from drskill import
loadout_wizard`) without forcing `cli.py` to load first. A top-level `from
drskill.cli import console` in `loadout_wizard.py` would risk a circular
import if `cli.py` ever happened to be mid-import when `loadout_wizard` is
imported directly (e.g. by the test module, which imports `loadout_wizard`
before `from drskill.cli import app`). I chose the non-circular option the
coordinator offered: `loadout_wizard.py` now constructs its own
`rich.console.Console()` instance at module level:

```python
console = Console()
```

with a comment in the source explaining why (not importing `cli.console`).
This mirrors `cli.py`'s own `console = Console()` pattern and needs no
lazy-import gymnastics inside `run()`.

**Guard.** No extra guard was needed: `run()` already exits via
`_stdin_is_tty()` before the scan begins, so the spinner never runs against
a non-tty stdout — consistent with the coordinator's note.

## 3 — Harness selection step

After the scan and the (now-deduped, see #4) candidate row list, `run()`
computes `all_harnesses = sorted({h for row in candidates for h in
row.harnesses})`:

- `--harness` given: filters rows exactly as before, no question asked.
- No `--harness` and `len(all_harnesses) > 1`: prompts via a new
  `_select_harness(harnesses)` helper — numbered list of sorted harness ids
  plus `a  all harnesses`, looping `typer.prompt` until a valid number or
  `"a"`. A number filters `candidates` to rows containing that harness in
  their (post-merge) harness set; `"a"` keeps everything.
- No `--harness` and one (or zero) harness in the set: no question, `rows =
  candidates` unchanged.

Badges always render the full per-row harness union regardless of which
path was taken, since `_render` reads `row.harnesses`, which is computed
once during row-building and never narrowed by the harness step (only the
row *list* is filtered, not any row's own badge data).

## 4 — Cross-harness dedup

`_build_rows` no longer builds one `_Row` per contributor. It first groups
non-system contributors by a merge key computed by `_group_key`:

- Tracked (`_is_tracked`: `manifest_build._SOURCE_TYPES.get(c.source.kind)
  is not None and bool(c.source.source)`, i.e. the same "is this local-only"
  test `manifest_build.contributors_to_manifest` itself uses, inverted):
  key = `("tracked", kind, manifest_build.normalize_name(name), source
  string)`.
- Local-only: key = `("local", kind, manifest_build.normalize_name(name),
  content_hash)`.

For each group, `_Row` now carries:
- `contributor`: the representative — `_pick_representative` prefers any
  tracked member over local-only ones (ties broken by `id` for
  determinism), used for the published entry, the display name, and the
  source-summary text.
- `harnesses`: `sorted({d.harness for m in members for d in
  m.deployments})` — the union across every member, so badges show every
  harness a skill is deployed to even though only the representative gets
  published.
- `scope`: `"project"` if any member is project-scoped, else `"user"` —
  drives both the section a row renders under and its preselection
  (`selected = (scope == "project")`), matching "preselect follows section
  as before."

`_render` and the harness-step/`--harness` filtering were updated to read
`row.harnesses` / `row.scope` instead of reaching into
`row.contributor.deployments` / `row.contributor.scope`, since the
representative no longer necessarily carries the full deployment set.

I chose to reference `manifest_build._SOURCE_TYPES` directly (a
module-private name) rather than duplicate the kind→source-type mapping as
a second constant in `loadout_wizard.py`. The project's ruff config has no
private-access rule enabled (default `E4/E7/E9/F` select only), so this
doesn't trip lint, and it keeps the "what counts as tracked" definition in
exactly one place instead of two copies that could drift.

## TDD evidence

Extended `contributor()` in `tests/test_loadout_wizard.py` with optional
`content_hash` and `id` overrides (both `None`-defaulted, so every existing
call site is untouched) so tests can construct two same-named contributors
with distinct ids/hashes for the dedup cases.

Added 8 tests, all written before the corresponding production change:
- `test_dedup_tracked_across_harnesses`
- `test_dedup_local_only_same_hash_merges`
- `test_dedup_local_only_different_hash_keeps_separate_rows`
- `test_dedup_merges_scope_prefers_project`
- `test_harness_step_prompts_when_multiple_harnesses`
- `test_harness_step_all_keeps_everything`
- `test_harness_step_absent_for_single_harness`
- `test_scan_status_spinner_does_not_crash`

RED, before implementing the harness-step/dedup changes (spinner change had
already been folded into the same edit; the four failures below are the
ones that actually exercise new behavior — the other four passed
incidentally against the old single-contributor-per-row code, which is
expected since they assert behavior that was already true, or asserts an
absence):

```
$ uv run pytest tests/test_loadout_wizard.py -q
...............FF.FF.......                                              [100%]
FAILED tests/test_loadout_wizard.py::test_dedup_tracked_across_harnesses
FAILED tests/test_loadout_wizard.py::test_dedup_local_only_same_hash_merges
FAILED tests/test_loadout_wizard.py::test_dedup_merges_scope_prefers_project
FAILED tests/test_loadout_wizard.py::test_harness_step_prompts_when_multiple_harnesses
4 failed, 23 passed in 0.31s
```

Representative failure (dedup wasn't merging rows yet, so two `alpha`
contributors published as two entries instead of one — the assertion that
actually pins the new behavior):
```
AssertionError: assert 2 == 1
 +  where 2 = len([{... 'name': 'untracked', ...}, {... 'name': 'untracked-2', ...}])
```

GREEN after implementing `_group_key`/`_pick_representative`/`_build_rows`
merge logic, `_select_harness`, and the spinner:

```
$ uv run pytest tests/test_loadout_wizard.py -q
...........................                                              [100%]
27 passed in 0.65s
```

## Verification

```
$ uv run pytest -q
809 passed, 11 warnings in 16.68s

$ uv run ruff check src/drskill
Found 2 errors.
```

The 2 ruff errors are the same pre-existing, unrelated ones noted in the
prior fix report (`checks/shadowing.py:8` `shlex` redefinition,
`report.py:5` unused `shlex` import) — untouched, out of scope for this fix.

```
$ uv run ruff check src/drskill/loadout_wizard.py tests/test_loadout_wizard.py
All checks passed!
```

## Self-review

- Diffed `src/drskill/loadout_wizard.py`: the only structural changes are
  the new `console`/`escape` imports and module-level `console = Console()`,
  the spinner wrapping the scan call, the harness-set computation and
  `_select_harness` prompt in `run()`, and the merge/grouping rewrite of
  `_build_rows` plus the `_render`/filter call sites updated to read
  `row.harnesses`/`row.scope`. `_selection_loop`, `_print_summary`,
  `_create_loadout`, and `_publish` are untouched.
- Confirmed existing single-harness fixtures (most of the test file) never
  hit the new harness-selection prompt, since their worlds only ever
  populate one harness id (`harnesses=("claude-code",)` default) — verified
  by inspection and by the fact those tests' `input=` sequences were not
  changed and still pass.
- Confirmed `--harness` continues to bypass the question entirely
  (`test_harness_filter_and_badges`, `test_unknown_harness_through_wizard`
  both pass unmodified) since the `if harness is not None:` branch in
  `run()` is checked before the `elif len(all_harnesses) > 1` branch that
  triggers the prompt.
- Confirmed via `git status` that only `src/drskill/loadout_wizard.py` and
  `tests/test_loadout_wizard.py` were staged for this commit; the
  pre-existing untracked files (`decoy-secret.txt`, `drskill.toml`,
  `initial_design_doc.md`, `.claude/`) were left alone.
- Noted but did not change: `run()`'s own `_stdin_is_tty()` guard still
  echoes `"--from-project needs an interactive terminal."`, a message that
  refers to a flag that no longer exists after the concurrent `--empty`
  rework — this guard is effectively unreachable via the CLI now (`cli.py`
  already checks `_stdin_is_tty()` before ever calling `run()`), and no
  test asserts this exact text, so leaving the message alone was the
  minimal-footprint choice; flagging it here in case a later pass wants to
  reword it for the current flag surface.

## Concerns

None blocking. Same out-of-scope, pre-existing ruff issues noted in the
prior report remain untouched. The stale `--from-project` wording noted
above is cosmetic dead code, not a behavior bug.

---

# Second walk-through fix report (2026-08-31)

Fixes for the two changes recorded in "Second walk-through revisions
(2026-08-31)" in `docs/superpowers/specs/2026-08-31-loadout-wizard-design.md`
(decisions 4 and 5). Branch `loadout-wizard`. Commit: `855944b` "feat: merge
skill variants by name and add arrow-key selection". Also folds in the
small cleanup item 3 from the coordinator's message (the stale
`--from-project` guard wording flagged, but left alone, in the prior
report's Concerns section).

Before starting, re-verified branch state: `git log` showed one new commit,
`833c76a docs: second walk-through spec revisions`, since the prior fix
round — a docs-only commit, no code changes. `src/drskill/loadout_wizard.py`
and `tests/test_loadout_wizard.py` matched exactly what this session last
committed (`a70bc77`), so no reconciliation was needed this time.

## 1 — Merge rows on (kind, normalized name) alone

`_group_key` in `src/drskill/loadout_wizard.py` dropped the two-branch
tracked/local scheme (which additionally keyed on provenance source or
content hash) down to a single key: `(c.kind,
manifest_build.normalize_name(c.name))`. Groups can now mix tracked and
local-only members. `_pick_representative` needed no code change — its
tracked-preference logic (`tracked = [m for m in members if
_is_tracked(m)]; pool = tracked or members; return sorted(pool, key=lambda
m: m.id)[0]`) already existed from the prior round but was previously dead
code, since the old key never let a tracked and local member land in the
same group. It's live now.

**Tests updated per the coordinator's instructions:**
- `test_dedup_local_only_different_hash_keeps_separate_rows` renamed to
  `test_dedup_local_only_different_hash_merges_deterministically` and
  rewritten: two local-only contributors with the same name but different
  content hashes now merge into one row (`len(entries) == 1`), and the
  representative is asserted deterministic — the published entry's
  `content_hash` matches the lexicographically-first id's contributor
  (`"/tmp/untracked-a"`, hash `"11"*32`), not the other one.
- New `test_dedup_merges_local_and_tracked_copies`: a local-only copy and a
  `gh-skill`-tracked copy of the same name (`"alpha"`) merge into one row;
  asserts the source summary in the rendered output shows the tracked
  source (`"friend/x@v1"` appears in `result.output`), and the published
  entry has `local_only: False` and `source_type: "github"` — i.e. the
  tracked member won as representative, not the local one.
- Existing "two genuinely different names never merge" coverage (e.g.
  `test_wizard_publishes_the_confirmed_selection`'s `alpha`/`beta` pair)
  needed no changes — different normalized names never share a group key
  regardless of the key scheme.

RED (before the `_group_key` change):
```
$ uv run pytest tests/test_loadout_wizard.py -q -k dedup
..FF.
AssertionError: assert 2 == 1
 +  where 2 = len([{... 'name': 'untracked', ...}, {... 'name': 'untracked-2', ...}])
2 failed, 3 passed, 23 deselected in 0.23s
```

GREEN after the `_group_key` change: `28 passed` (see full-suite run below).

## 2 — Arrow-key selector with automatic fallback

Added to `src/drskill/loadout_wizard.py`, structured exactly as specified —
a pure state machine plus an IO shell:

- **`_SelectState`** (new dataclass, next to `_Row`): `rows`, `cursor: int =
  0`, `offset: int = 0` (scroll window top). `_WINDOW = 15` is a
  module-level constant.
- **`_apply_key(state, key) -> "accept" | "abort" | None`** — pure: `"up"`/
  `"k"` and `"down"`/`"j"` move and clamp the cursor at both ends; `"space"`
  toggles the cursored row; `"a"`/`"n"` set/clear all; `"enter"` always
  returns `"accept"` regardless of selection count (the zero-selection
  check still lives in `run()`, unchanged); `"q"` returns `"abort"`. After
  any cursor move, offset is adjusted to keep the cursor inside the
  `_WINDOW`-row scroll window, then clamped to `[0, max(0, n -
  _WINDOW)]` so it never over-scrolls past the end of a short list.
- **`_getkey() -> str`** — the IO seam, assuming stdin is already in
  cbreak mode (set by the caller): reads one byte, decodes `ESC [ A` →
  `"up"`, `ESC [ B` → `"down"`, `\r`/`\n` → `"enter"`, `" "` → `"space"`,
  anything else lowercased, unrecognized escape sequences → `"other"`
  (silently ignored by `_apply_key`, which has no branch for it).
  Monkeypatchable by name (`monkeypatch.setattr(loadout_wizard, "_getkey",
  ...)`), used directly in tests to script key sequences.
- **`_select_frame(state) -> str`** (new helper, not explicitly named in
  the spec but needed to make `_interactive_select` testable/reviewable in
  isolation): builds the full multi-line render — the header line (`"N
  selected · arrows/jk move · space toggle · a all · n none · enter accept
  · q abort"`), a `"… N more above"` line when `offset > 0`, the visible
  window with section headers on scope changes, `>`/` ` cursor marker,
  `[x]`/`[ ]` selection marker, and harness badges, then `"… M more
  below"` when rows extend past the window. Returns one string so the
  caller can count `\n`s to know how many terminal lines to erase before
  redrawing.
- **`_interactive_select(rows)`** — the shell: builds a `_SelectState`,
  hides the cursor (`\x1b[?25l`), enters `try`/`finally`, calls
  `tty.setcbreak(fd)` once, then loops: render the frame (moving the
  cursor up `\x1b[{n}A\x1b[J` before redrawing on every iteration after the
  first), call `_getkey()`, apply it via `_apply_key`, and either `return`
  on `"accept"` or print `"Aborted."` and `raise typer.Exit(0)` on
  `"abort"`. The `finally` block restores the original termios attributes
  (`termios.tcsetattr(fd, termios.TCSADRAIN, old)`) and shows the cursor
  again (`\x1b[?25h`) unconditionally, including on the abort path (Python
  runs `finally` before the `typer.Exit` propagates).
- **`_select_rows(rows)`** — the fallback chooser, now called from `run()`
  in place of the old direct `_selection_loop(rows)` call: tries the
  termios path only when `sys.stdin.isatty()` is true and a
  `termios.tcgetattr(fd)` preflight succeeds; any exception during that
  preflight (including `sys.stdin.fileno()` itself raising, which happens
  under pytest's captured stdin) falls back to `_selection_loop(rows)`,
  which is otherwise completely unchanged.

**Module import.** `termios`/`tty` are imported once at module level inside
a `try`/`except ImportError`, falling back to `termios = tty = None` on
non-POSIX platforms (e.g. Windows) so importing `loadout_wizard` itself
never crashes there — `_select_rows`'s `termios is not None` check is the
first line of defense, backed by the broader preflight try/except.

**Tests**, all written before the corresponding implementation:
- `test_apply_key_moves_cursor_and_clamps` — up/down/j/k movement and
  clamping at both list ends, with fabricated `_Row`s via a new `_row()`
  test helper (bypasses `_build_rows`/scanning entirely).
- `test_apply_key_scrolls_offset_beyond_window` — walks the cursor down
  through a 20-row list (`_WINDOW + 5`), asserting the offset scrolls by
  exactly one when the cursor first exceeds the window, that offset clamps
  at `len(rows) - _WINDOW` once the cursor reaches the last row even after
  many more "down" presses, and that walking back "up" to the top returns
  offset to 0.
- `test_apply_key_toggle_all_none` — space toggles only the cursored row;
  `a`/`n` set/clear every row.
- `test_apply_key_accept_abort` — `"enter"` → `"accept"`, `"q"` →
  `"abort"`.
- `test_select_rows_falls_back_when_termios_setup_fails` — calls
  `_select_rows` directly (no CliRunner): monkeypatches `sys.stdin.isatty`
  to `True` and `termios.tcgetattr` to raise, monkeypatches
  `loadout_wizard._selection_loop` to a recording stub, and asserts the
  stub was called with the same `rows` list — confirming the fallback path
  without needing a real (or fake) pty.
- `test_interactive_select_applies_scripted_keys` — calls
  `_interactive_select` directly, monkeypatching `sys.stdin.fileno`,
  `termios.tcgetattr`/`tcsetattr`, and `loadout_wizard.tty.setcbreak` to
  no-ops (pytest's captured stdin has no real fd and isn't a tty, so these
  stand in for the real terminal layer), and scripts `_getkey` to yield
  `"down"`, `"space"`, `"enter"` in sequence. Asserts the first row stays
  unselected and the second (now-cursored) row ends up selected — the
  state-machine-through-the-shell integration the coordinator asked for.
- `test_interactive_select_abort_raises_exit` — same termios/tty
  monkeypatching, scripts a single `"q"`, and asserts `_interactive_select`
  raises `typer.Exit` with `exit_code == 0`. (Note: `typer.Exit` is a
  `RuntimeError` subclass, not `SystemExit` — confirmed via
  `typer.Exit.__mro__` before writing the assertion, since a first draft of
  this test incorrectly expected `SystemExit`.)

All existing CliRunner-driven wizard tests were re-run unmodified and
continue to pass, confirming they still exercise `_selection_loop` through
the fallback: pytest's `CliRunner`-provided stdin is never a real tty, so
`sys.stdin.isatty()` is `False` for every one of them regardless of the
`wizard_env` fixture's `_stdin_is_tty` monkeypatch (that monkeypatch only
covers the top-level `run()` guard, not `_select_rows`'s independent
`sys.stdin.isatty()` check).

RED (before adding `_SelectState`/`_apply_key`/`_select_rows`/
`_interactive_select`/`_getkey`):
```
$ uv run pytest tests/test_loadout_wizard.py -q -k "apply_key or select_rows or interactive_select"
7 failed, 28 deselected in 0.15s
AttributeError: module 'drskill.loadout_wizard' has no attribute '_WINDOW'
AttributeError: module 'drskill.loadout_wizard' has no attribute '_SelectState'
AttributeError: module 'drskill.loadout_wizard' has no attribute '_select_rows'
AttributeError: module 'drskill.loadout_wizard' has no attribute 'tty'
```

First implementation pass left two of those seven red for an unrelated
reason — pytest's captured stdin (`_pytest.capture.DontReadFromInput`) has
no `.fileno()`, which `_interactive_select` calls unconditionally:
```
io.UnsupportedOperation: redirected stdin is pseudofile, has no fileno()
```
Fixed by adding `monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)` to
both of the two tests that call `_interactive_select` directly.

GREEN: `35 passed` (see full-suite run below).

## 3 — Guard message cleanup

`run()`'s `_stdin_is_tty()` guard (flagged as stale in the prior report,
since it referenced the now-removed `--from-project` flag) now reads:
```python
if not _stdin_is_tty():
    # Defense-in-depth: cli.py already checks _stdin_is_tty() before
    # ever calling run(), so this should be unreachable in practice.
    typer.echo("The interactive picker needs a terminal.")
    raise typer.Exit(1)
```
matching the current flag surface (interactive-by-default, `--empty` to
opt out). The guard itself is unchanged — still present, still checked
first. No test asserted the old wording, so nothing else needed updating.

## Verification

```
$ uv run pytest tests/test_loadout_wizard.py -q
...................................
35 passed in 0.28s

$ uv run pytest -q
817 passed, 11 warnings in 16.09s

$ uv run ruff check src/drskill
Found 2 errors.
```

The 2 ruff errors are the same pre-existing, unrelated ones noted in both
prior fix reports (`checks/shadowing.py:8` `shlex` redefinition,
`report.py:5` unused `shlex` import) — still untouched, still out of scope.

```
$ uv run ruff check src/drskill/loadout_wizard.py tests/test_loadout_wizard.py
All checks passed!
```

(817 = 809 from the prior report + 8 new tests: 2 dedup tests for item 1,
6 arrow-key-selector tests for item 2.)

## Self-review

- Diffed `src/drskill/loadout_wizard.py`: the only changes are the
  `_group_key` simplification (no other line in `_build_rows` or
  `_pick_representative` needed to change), the module-level
  `termios`/`tty` guarded import, the new `_SelectState` dataclass and
  `_WINDOW` constant, the five new selector functions
  (`_select_rows`/`_apply_key`/`_getkey`/`_select_frame`/
  `_interactive_select`) inserted between `_render` and the unchanged
  `_selection_loop`, the one-line swap of `_selection_loop(rows)` to
  `_select_rows(rows)` in `run()`, and the reworded guard message.
  `_render`, `_print_summary`, `_create_loadout`, `_publish`,
  `_select_harness`, and `_selection_loop` are byte-for-byte unchanged.
- Confirmed `_pick_representative`'s tie-break is exercised for real now
  (previously unreachable): `test_dedup_local_only_different_hash_merges_deterministically`
  pins the "first member by id, no tracked member present" branch, and
  `test_dedup_merges_local_and_tracked_copies` pins the "prefer the
  tracked member" branch.
- Confirmed the harness-selection step (from the prior round) still reads
  `row.harnesses`, unaffected by the `_group_key` change — merge groups
  still union their members' harnesses the same way regardless of which
  key produced the group.
- Confirmed via `git status` that only `src/drskill/loadout_wizard.py` and
  `tests/test_loadout_wizard.py` were staged for this commit; the
  pre-existing untracked files (`decoy-secret.txt`, `drskill.toml`,
  `initial_design_doc.md`, `.claude/`) were left alone, and the new
  `test_interactive_select_*` tests' termios monkeypatches (which patch
  the shared, process-global `termios`/`tty` module objects) are unwound
  automatically by pytest's `monkeypatch` fixture at the end of each test,
  so they don't leak into other tests in the same run — verified by the
  full-suite pass immediately after.

## Concerns

None blocking. Two things worth flagging for a future pass, not fixed here
since they're outside what was asked:
- `_select_rows`'s preflight only guards the initial
  `termios.tcgetattr(fd)` probe. If `tty.setcbreak(fd)` or a later
  `termios.tcsetattr` call inside `_interactive_select` itself were to
  raise (e.g. the terminal state changes mid-session), that exception
  would propagate uncaught rather than falling back to the numbered
  prompt. This matches the coordinator's literal spec ("ANY exception
  during setup → fall back", read as the preflight setup, not the whole
  session) but is narrower than "never crash regardless of what the
  terminal does."
- Same pre-existing, out-of-scope ruff issues in `checks/shadowing.py` and
  `report.py` remain untouched, as in both prior reports.

---

# Third walk-through fix report (2026-08-31)

Fixes for decisions 6 and 7 recorded in "Third walk-through revisions
(2026-08-31)" in `docs/superpowers/specs/2026-08-31-loadout-wizard-design.md`:
replace the hand-rolled termios selector with questionary, and a
readability pass on stage headings, row labels, and result lines. Branch
`loadout-wizard`. Commit: `e0b6b55` "feat: questionary-driven selection
with a readability pass".

Before starting, re-verified branch state: one new commit since the last
round, `6c70c3c docs: third walk-through spec revisions (questionary)`,
docs-only. `src/drskill/loadout_wizard.py` and `tests/test_loadout_wizard.py`
matched exactly what this session last committed (`855944b`), so no
reconciliation was needed.

## 1 — Dependency

`git diff uv.lock` was clean before running `uv add questionary` (nothing
to report). Ran:
```
$ uv add questionary
Resolved 88 packages in 254ms
 + prompt-toolkit==3.0.53
 + questionary==2.1.1
 + wcwidth==0.8.3
```
`pyproject.toml` gained one line (`"questionary>=2.1.1",` in
`dependencies`); `uv.lock` picked up questionary, prompt_toolkit, and
wcwidth as expected — all pure Python, no new native deps. Both committed
together with the code in `e0b6b55`. Verified the import resolves:
```
$ uv run python -c "import questionary"
questionary import OK
```

## 2 — questionary seams, replacing the termios selector

Deleted exactly the list the coordinator specified from
`src/drskill/loadout_wizard.py`: `_interactive_select`, `_apply_key`,
`_getkey`, `_SelectState`, `_select_rows`, `_selection_loop`,
`_select_harness`, the `_WINDOW` constant, and the guarded
`termios`/`tty` imports. Also removed `_render`, which the coordinator's
delete list didn't name explicitly but which was `_selection_loop`'s only
caller and became dead code once `_selection_loop` was gone — kept out for
the same "no unused code" reasoning as everything else on the list.

Added two new seams:
- **`_choose_harness(harness_ids: list[str]) -> str | None`** —
  `questionary.select("Which harness should this loadout draw from?",
  choices=sorted(harness_ids) + ["All harnesses"])`. Returns the chosen id,
  or `None` for "All harnesses". `answer is None` (questionary's own
  Ctrl-C handling — `.ask()` catches `KeyboardInterrupt` and returns `None`
  by default) prints `"Aborted."` and raises `typer.Exit(0)`.
- **`_choose_skills(rows, chosen_harness) -> list[_Row]`** —
  `questionary.checkbox` with one `questionary.Choice(title=..., checked=
  row.selected, value=row)` per row, `questionary.Separator("— Project
  scope —")` / `"— User scope —"` inserted whenever the scope changes
  across the (already scope-sorted) row list. Same Ctrl-C-as-abort
  handling. Returns the list of `_Row` objects the user left checked.

**Deviation from the literal spec, documented here as instructed:**
`_choose_skills`'s given signature was `(rows: list[_Row]) -> list[_Row]`
— one argument. I extended it to `(rows, chosen_harness)` because
`_row_label` (see #3) needs `chosen_harness` to decide whether to render a
badge, and `_choose_skills` is what builds each `Choice.title` by calling
`_row_label` per row — there was no other place for that value to reach
`_row_label` without either a module-level mutable global (bad) or
threading it through the one function that actually builds the labels.
`run()` already computes `chosen` (from `--harness`, the picker, or
`None` when there's only one harness) right before calling
`_choose_skills`, so passing it through was the minimal, most literal
extension available. This is a strict superset of the specified contract
(rows is still the first and only *required* concept), not a redesign.

`run()` no longer calls `_select_rows`; it calls `_choose_harness` (only
when `--harness` wasn't given and more than one harness exists across the
merged candidate rows — unchanged condition from the prior round) and
always calls `_choose_skills`. The zero-selection check
(`if not selected: ... "Nothing selected." ... typer.Exit(1)`) stays,
now checking the list `_choose_skills` returned instead of filtering
`row.selected` after a mutating selection loop.

## 3 — Readability pass

- **`_row_label(row, chosen_harness, width=24) -> str`** (pure): builds
  one line — the name left-justified to `width`, two spaces, the source
  (or `"local only"`) with any `==<version>` suffix stripped via a
  module-level compiled regex (`_VERSION_SUFFIX = re.compile(r"==.*$")`),
  then a badge. Badge rule: nothing when `chosen_harness is not None`;
  when `None`, `[a, b]` for 1–2 harnesses, `[first +N]` (N = harness count
  minus one) for more than 2.
  **Second deviation, also documented as instructed:** the given
  signature was `(row, chosen_harness: str | None) -> str` — no width
  parameter. I added `width` as an optional keyword (default 24, the same
  value as the floor) because the spec's own description — "computed
  column width (min 24, max longest name capped 34)" — only makes sense as
  a value shared across every row in the visible list (the "longest name"
  phrase implies comparing across rows), and a strictly two-argument pure
  function can't express that without either recomputing a shared value
  redundantly inside every call or accepting it as an argument. I added a
  companion `_label_width(rows) -> int` that computes
  `min(max(24, longest_name_length), 34)` once per `_choose_skills` call
  and passes it through. Both `_row_label` and `_label_width` are directly
  unit-tested (alignment at the floor, no truncation of names past the
  cap, version stripping, sources without a version suffix left alone,
  badge absence when a harness is chosen, 1–2-harness list badges, the
  >2-harness compact badge, and the floor/cap behavior of `_label_width`
  itself, including the empty-list case).
- **Stage headings**: `"Pick a harness"` (only printed right before the
  harness picker, when it runs at all), `"Pick skills"` / `"Pick skills —
  from {chosen}"`, and `"Summary — {N} entries, {M} local-only"` (the `, M
  local-only` clause omitted entirely when `M == 0`, matching the
  coordinator's mock) are printed via `console.print` with a leading blank
  line and `[bold]...[/bold]` markup, escaped via `rich.markup.escape`.
  These live in `run()`/`_print_summary`, not inside the seam functions —
  necessary so CliRunner tests that monkeypatch `_choose_harness`/
  `_choose_skills` wholesale still see the heading text in `result.output`
  (a heading printed *inside* a faked-out seam would never run).
- **Summary entries**: the per-entry marker text changed from
  `"local-only"` to `"local only"` (two words, matching the coordinator's
  mock exactly) — the summary itself still shows the full, unstripped
  `source_reference` (no version stripping there, only in the list view,
  per the spec: "kept in the summary"). Notes are now printed via
  `console.print` with `[dim]...[/dim]`.
- **Result lines**: `_create_loadout`'s success path now prints `"\n[green]
  ✓[/green] Created {ref}"` (the one blank line before the whole result
  block lives here, since it's the first of the two result lines) and
  `_publish`'s success path prints `"[green]✓[/green] Published revision
  {number} ({hash})"`. The failure paths in both functions are completely
  unchanged — no checkmark, same wording, since a failure isn't the "here's
  what happened" result line the mock is describing.
- **What I deliberately did NOT implement**: the coordinator's item 7 also
  describes badges as "rendered compact and dim" and sources as "dim" in
  the list view. `_row_label` returns a plain `str` per its specified
  signature, and questionary/prompt_toolkit widgets don't render rich
  markup at all — `[dim]...[/dim]` only means something to `console.print`
  calls through `rich`, which never touches a `questionary.Choice.title`.
  Achieving real dimming inside the checkbox list would require building
  prompt_toolkit-style `List[Tuple[str, str]]` formatted text instead of a
  plain string, which conflicts with `_row_label`'s given `-> str`
  contract and, more importantly, with "unit-test this function directly"
  being straightforward plain-string assertions. I chose to honor the
  literal, testable contract over the cosmetic dimming inside the
  interactive widget and flag the gap here rather than silently drop it or
  silently reinterpret the signature.
- The scan spinner (`console.status(...)`, from the first fix round) is
  unchanged, as instructed.

## 4 — Rewired tests

Every test in `tests/test_loadout_wizard.py` that previously drove
selection via `input=` through the deleted numbered loop was rewired to
monkeypatch `_choose_harness`/`_choose_skills` with small canned
callables — `typer.confirm` still goes through `input=` since it's
untouched. Added three reusable fakes near the top of the file:
`_accept_preselected(rows, chosen)` (returns the rows already marked
`.selected`, standing in for a user who accepts questionary's pre-checked
defaults), `_accept_all(rows, chosen)` (returns every row, including
unselected ones), and `_pick_all_harnesses(harness_ids)` (returns `None`,
standing in for choosing "All harnesses").

**Tests that needed behavioral, not just mechanical, rewrites** (the ones
that used to assert on rendered `[x]`/`[ ]`/badge text in
`result.output`, which the real numbered-loop renderer produced but which
a monkeypatched-away questionary widget never prints):
- `test_sections_and_preselection`: now spies on the `rows` list passed
  into `_choose_skills` and asserts `[row.scope for row in rows] ==
  ["project", "user"]` (section ordering) plus `proj_row.selected is True`
  / `user_row.selected is False` directly (preselection), instead of
  parsing `[x]`/`[ ]` substrings out of printed output. This is exactly
  the "expose that via a tiny pure helper if needed, e.g. test
  `_row_label`/row.selected directly" alternative the coordinator
  suggested.
- `test_harness_filter_and_badges`: still asserts the behavioral filtering
  (`pi-only` excluded from the rows reaching the picker, `cc-only` +
  `both` published), but the badge assertion changed from `"[claude-code,
  pi]" in result.output` to calling `loadout_wizard._row_label(both_row,
  captured["chosen"])` directly and asserting `"["` is absent — because
  with `--harness claude-code` given, `chosen_harness` is
  `"claude-code"` (not `None`), and per the new badge rule that suppresses
  badges entirely once a harness is chosen. This is a genuine, spec-driven
  behavior change from the previous round (which always showed the full
  harness union regardless of filtering) — not a test-only adjustment.
- `test_dedup_tracked_across_harnesses` and
  `test_dedup_merges_scope_prefers_project`: both span two harnesses, so
  now also monkeypatch `_choose_harness` to `_pick_all_harnesses` (keeping
  "all harnesses", matching their old `"a\n"` input) and inspect the
  spied `rows`/`_row_label` output instead of `result.output` text.
- `test_harness_step_prompts_when_multiple_harnesses`: now fakes
  `_choose_harness` to record the `harness_ids` it was called with and
  return `"claude-code"` directly (simulating the picker choice),
  replacing the old `"1\n"` numbered-prompt input.
- `test_harness_step_absent_for_single_harness`: now monkeypatches
  `_choose_harness` to a function that raises `AssertionError` if called
  at all, proving the picker never runs for a single-harness world —
  stronger than the old "the heading text isn't in the output" check,
  which would have silently passed even if the function ran but printed
  nothing.

**Deleted**: `test_apply_key_moves_cursor_and_clamps`,
`test_apply_key_scrolls_offset_beyond_window`,
`test_apply_key_toggle_all_none`, `test_apply_key_accept_abort`,
`test_select_rows_falls_back_when_termios_setup_fails`,
`test_interactive_select_applies_scripted_keys`,
`test_interactive_select_abort_raises_exit` — all exercised
now-deleted code. The `import sys` and `import termios` at the top of the
test file were removed along with them (no longer referenced anywhere).

**Added**: 8 new tests directly unit-testing `_row_label`/`_label_width`
per the coordinator's instruction (alignment at the floor width, no
truncation past the cap, version-suffix stripping, sources without a
version suffix left alone, no badge when a harness is chosen, 1–2-harness
list badges, the compact >2-harness badge, and `_label_width`'s floor/cap/
empty-list behavior).

**Kept, unchanged**: `test_system_contributors_are_skipped`,
`test_unknown_harness_through_wizard`,
`test_non_tty_falls_back_to_plain_create`,
`test_empty_flag_skips_the_wizard_interactively`,
`test_wizard_flags_error_when_wizard_cannot_run`,
`test_plain_create_still_works` — none of these ever reach the selection
seams (they exit earlier, in `cli.py`'s own flag validation or the
`_stdin_is_tty()`/`--empty` fallthrough to plain `create`), so nothing
about this round's changes touches them.

**Behavioral coverage preserved** (all still asserted, just through
different mechanics): dedup (tracked-across-harnesses, local-only same/
different hash, local+tracked merge, scope-prefers-project), harness
filter/badge rules (now split between an end-to-end filter-behavior
assertion and dedicated `_row_label` unit tests), zero-selection, decline,
publish-failure-with-manifest-recovery, manifest-out
write-after-confirm ordering (both the write and the not-written-on-
decline cases), and the local-only/notes warning text.

### Verification note on TDD

This was a full architectural swap (one rendering/interaction mechanism
deleted, a different library's mechanism substituted) rather than an
additive behavior change, so the implementation and most of the rewired
tests were written together in one pass — a strict per-test RED-then-GREEN
cycle wasn't meaningful for tests that only needed their monkeypatch
target renamed. Before every existing test's mechanical rewiring, the
functions it now references (`_choose_harness`, `_choose_skills`,
`_row_label`, `_label_width`) did not exist, so the whole suite failed to
collect (`AttributeError`) until the implementation landed — that is the
RED state for the mechanical rewiring. For the two behavior changes that
are genuinely new logic (badge suppression when a harness is chosen; the
version-suffix strip), I additionally confirmed the tests aren't vacuous
by exercising `_row_label` directly outside pytest before and after
reasoning through the logic, e.g.:
```
$ uv run python -c "... _row_label(row, None) ..."
'alpha                     friend/x@v1  [claude-code, codex]'
$ uv run python -c "... _row_label(row, 'claude-code') ..."
'alpha                     friend/x@v1'
```
confirming the badge really does appear/disappear based on
`chosen_harness`, not just because the test happened to pass.

## Verification

```
$ uv run pytest tests/test_loadout_wizard.py -q
....................................
36 passed in 0.36s

$ uv run pytest -q
818 passed, 11 warnings in 16.23s

$ uv run ruff check src/drskill
Found 2 errors.
```

The 2 ruff errors are the same pre-existing, unrelated ones noted in all
three prior fix reports (`checks/shadowing.py:8` `shlex` redefinition,
`report.py:5` unused `shlex` import) — still untouched, still out of
scope.

```
$ uv run ruff check src/drskill/loadout_wizard.py tests/test_loadout_wizard.py
All checks passed!

$ uv run python -c "import questionary"
questionary import OK
```

Also confirmed real `questionary.checkbox`/`questionary.select` widget
construction (not `.ask()`, just building the `Question` object) succeeds
against representative `_Row` data and that `.ask()` returning `None`
(the Ctrl-C case) is handled correctly by both seams — both checked
outside pytest via ad hoc scripts, since driving the widgets' real
interactive rendering isn't practical or requested under CliRunner.

## Self-review

- Diffed `src/drskill/loadout_wizard.py`: confirmed `_is_tracked`,
  `_group_key`, `_pick_representative`, and `_build_rows` (the dedup
  logic from the last two rounds) are byte-for-byte unchanged — this round
  only touched selection/rendering, not the merge logic.
- Confirmed `_create_loadout`/`_publish`'s failure paths are unchanged
  (still plain `typer.echo`, still the exact wording
  `test_publish_failure_reports_created_but_empty` and
  `test_create_failure_stops_before_publish` assert on) — only the
  success tails gained the checkmark treatment.
- Confirmed via `git status` that only `pyproject.toml`, `uv.lock`,
  `src/drskill/loadout_wizard.py`, and `tests/test_loadout_wizard.py` were
  staged for this commit; the pre-existing untracked files
  (`decoy-secret.txt`, `drskill.toml`, `initial_design_doc.md`,
  `.claude/`) were left alone.
- Re-checked `_choose_skills`'s Separator label text
  (`"— Project scope —"` / `"— User scope —"`) against the coordinator's
  literal spec text — matches exactly, including the em dashes.
- Re-read the two documented signature deviations (`_choose_skills`'s
  added `chosen_harness` parameter, `_row_label`'s added `width` keyword)
  once more against the coordinator's stated signatures before committing,
  to make sure neither was a shortcut but a build-necessity: `_row_label`
  literally cannot suppress or render a harness badge without knowing
  `chosen_harness`, and cannot express a shared list-wide alignment column
  without knowing `width` from somewhere outside a single row — both gaps
  trace directly back to the two-argument signature the spec gave
  `_row_label` itself, not to a design choice I introduced independently.

## Concerns

None blocking. Two things worth flagging:
- The "dim" styling on sources/badges in the interactive checkbox list
  (item 7) was not implemented — see the explanation in section 3 above.
  If real dimming inside the widget is wanted later, it requires
  `_row_label` (or a sibling function) to return prompt_toolkit-style
  formatted text tuples instead of a plain string, which is a larger,
  separately-reviewable change.
- Same pre-existing, out-of-scope ruff issues in `checks/shadowing.py` and
  `report.py` remain untouched, as in all three prior reports.

---

# Wave 3 re-review fix report: widget-level coverage (2026-08-31)

The re-review approved wave 3's behavior but flagged a real coverage gap:
because every wave-3 test monkeypatched `_choose_harness`/`_choose_skills`
themselves (replacing the seams wholesale), nothing actually exercised the
seams' own bodies — the questionary widget construction (Separator
placement, `checked=`, `value=` identity) and the Ctrl-C-abort branches
were untested. This round adds tests that patch `questionary.select`/
`questionary.checkbox` instead (the module attributes as imported into
`loadout_wizard`), driving the real seam functions with a fake `.ask()`.
Branch `loadout-wizard`. Commit: `1b9e844` "test: cover the questionary
glue and abort paths".

Re-verified branch state before starting: no new commits since `e0b6b55`
(this session's own last commit), so no reconciliation needed.

## Tests added

All in `tests/test_loadout_wizard.py`. A `_FakeQuestion` class (per the
coordinator's exact snippet) stands in for questionary's `Question`
object — `.ask()` returns a canned answer instead of driving a real
prompt. Added `import questionary` at module level so tests can
monkeypatch `questionary.select`/`questionary.checkbox` directly (the same
module object `loadout_wizard.py` calls through, since it does
`import questionary` too, not `from questionary import select`).

**`_choose_harness`** (3 tests):
- `test_choose_harness_builds_choices_and_returns_selection` — fakes
  `questionary.select` to capture its `message`/`choices` kwargs; asserts
  `choices == ["claude-code", "pi", "All harnesses"]` (sorted ids first,
  then the literal "All harnesses" appended) and that a `"claude-code"`
  answer passes straight through as the return value.
- `test_choose_harness_all_harnesses_returns_none` — a `"All harnesses"`
  answer maps to `None`.
- `test_choose_harness_ctrl_c_aborts` — an answer of `None` (questionary's
  own Ctrl-C signal) is asserted via `pytest.raises(typer.Exit)` +
  `capsys`, checking both `exit_code == 0` and that `"Aborted."` was
  printed — calling `_choose_harness` directly, no CliRunner needed.

**`_choose_skills`** (3 tests):
- `test_choose_skills_builds_choices_with_separator_and_checked_state` —
  builds one project-scope row (preselected) and one user-scope row (not),
  fakes `questionary.checkbox` to capture the `choices` list and return
  `[user_row]`. Asserts: exactly one `questionary.Separator` in the built
  choices (see the defect below), the project `Choice.checked is True` and
  the user `Choice.checked is False`, each `Choice.value is` the exact
  `_Row` object passed in (identity, not equality), the separator sits
  positionally between the two `Choice`s, and `_choose_skills` returns
  `[user_row]` — the exact object questionary "returned".
- `test_choose_skills_ctrl_c_aborts` — same pattern as the harness abort
  test, calling `_choose_skills` directly.
- `test_choose_skills_zero_selection_passes_through` — `.ask()` returning
  `[]` makes `_choose_skills` return `[]` unchanged; the "Nothing
  selected." handling is confirmed to live in `run()`, not the seam
  (matches the coordinator's note).

**End-to-end** (1 test):
- `test_end_to_end_through_real_seams_with_only_questionary_patched` —
  patches only `questionary.select`/`questionary.checkbox` (not the
  seams), using a fake `select` that answers `"claude-code"` and a fake
  `checkbox` that reads the *real* `choices` list `_choose_skills` built
  and returns whichever `Choice.value`s were pre-checked (mirroring a user
  who accepts the defaults). Runs the full `create` command via
  `CliRunner` and asserts the published entry is exactly `["cc-skill"]`
  (the codex-scoped skill filtered out by the harness choice) — proving
  `run()` → `_choose_harness`/`_choose_skills` → `questionary` round-trips
  correctly with no seam-level shortcut.

## A real defect surfaced and fixed

`test_choose_skills_builds_choices_with_separator_and_checked_state`
failed on first run:
```
AssertionError: assert 2 == 1
 +  where 2 = len([<Separator ...>, <Separator ...>])
```
`_choose_skills`'s loop inserted a `questionary.Separator` every time
`row.scope != current_scope`, including the very first row (`current_scope`
starts at `None`, which never equals `"project"` or `"user"`), so a
two-scope list produced *two* separators — one before the project group
and one before the user group — instead of one divider sitting between
them. Fixed by only appending a separator when `current_scope is not None`
(i.e. on an actual transition between two already-seen groups, not before
the first group):
```diff
     for row in rows:
         if row.scope != current_scope:
-            current_scope = row.scope
-            choices.append(questionary.Separator(
-                "— Project scope —" if current_scope == "project" else "— User scope —"
-            ))
+            if current_scope is not None:
+                choices.append(questionary.Separator(
+                    "— Project scope —" if row.scope == "project" else "— User scope —"
+                ))
+            current_scope = row.scope
```
This is a small, real behavior fix (one fewer divider line printed at the
very top of the skill list, above the first — and by row-sort-order
always project — group), not a test-only adjustment: the old code was
functionally producing a redundant "— Project scope —" header immediately
under the "Pick skills" heading with nothing above it to divide from. A
world with only one scope present (all project or all user, the common
case for small projects) still gets zero separators either way, unaffected
by this fix. Confirmed via `git diff` that this is the only change to
`src/drskill/loadout_wizard.py` this round — no other production code was
touched.

## A test bug caught and fixed (not a production defect)

The same test's first draft also failed after the separator fix, on a
different assertion:
```
AssertionError: assert 3 == 2
 +  where 3 = len([<Choice>, <Separator>, <Choice>])
```
`questionary.Separator` subclasses `questionary.Choice` (confirmed via
`issubclass(questionary.Separator, questionary.Choice)` → `True`), so
`isinstance(c, questionary.Choice)` matches separators too. Fixed the
test's own filter to explicitly exclude separators:
```python
choice_items = [
    c for c in choices
    if isinstance(c, questionary.Choice) and not isinstance(c, questionary.Separator)
]
```
No production code was involved in this one — purely a test-authoring
mistake, caught immediately by running the test. (Checked whether the
same trap affected the end-to-end test's `fake_checkbox`, which filters
`choices` on `c.checked` after an `isinstance(c, questionary.Choice)`
check: `questionary.Separator` instances default `checked=False`, so that
filter was safe as originally written and needed no change.)

## Verification

```
$ uv run pytest tests/test_loadout_wizard.py -q
...........................................
43 passed in 0.71s

$ uv run pytest -q
825 passed, 11 warnings in 15.57s

$ uv run ruff check src/drskill
Found 2 errors.
```
Same 2 pre-existing, unrelated ruff errors as all four prior reports
(`checks/shadowing.py:8`, `report.py:5`) — untouched.
```
$ uv run ruff check src/drskill/loadout_wizard.py tests/test_loadout_wizard.py
All checks passed!
```

(825 = 818 from the prior report + 7 new tests: 3 for `_choose_harness`,
3 for `_choose_skills`, 1 end-to-end.)

## Self-review

- Confirmed the `git diff` on `src/drskill/loadout_wizard.py` is exactly
  the one separator-transition fix shown above — nothing else in `run()`,
  `_choose_harness`, `_row_label`, `_label_width`, `_build_rows`, or the
  dedup logic changed.
- Re-ran the *entire* wave-3 test suite (not just the new tests) after the
  fix to confirm the separator change didn't break any of the seam-level
  monkeypatch tests from the last round, which never inspected separator
  counts and were unaffected — confirmed by the full 43/43 pass.
- Verified `questionary.select`/`questionary.checkbox` are patched via the
  `questionary` module object (`monkeypatch.setattr(questionary, "select",
  ...)`), the same object `loadout_wizard.py` holds a reference to via its
  own `import questionary` — not a separate copy — so the patch actually
  reaches the code under test. Confirmed by every new test passing (a
  patch on the wrong object would have left the real `.ask()` hang or
  raise under CliRunner's non-tty stdin instead of returning the canned
  answer).
- Confirmed via `git status` that only `src/drskill/loadout_wizard.py` and
  `tests/test_loadout_wizard.py` were staged for this commit; the
  pre-existing untracked files (`decoy-secret.txt`, `drskill.toml`,
  `initial_design_doc.md`, `.claude/`) were left alone.

## Concerns

None blocking. Same pre-existing, out-of-scope ruff issues in
`checks/shadowing.py` and `report.py` remain untouched, as in all four
prior reports. The "dim" styling gap noted in the wave-3 report is
unrelated to this round and still open if wanted later.
