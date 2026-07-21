# The review command

Date: 2026-07-20
Status: approved
Parent documents: `initial_design_doc.md`, `docs/superpowers/specs/2026-07-20-report-triage-design.md`

This is the second of two specs for cycle 3 of the v0.2 release. It depends on the first spec's report ordering, ack routing, and single-finding rendering.

## Why

The intended loop is scan, judge, ack. Today judging nine findings means reading a long report and hand typing nine ack commands. `drskill review` walks the findings one at a time and turns each decision into one keypress. First triage of a loadout should take about a minute.

The command is interactive by design, and agents must never get trapped in it. `scan` never prompts under any circumstances, and `review` refuses to start without a real terminal.

## The guard

`review` starts only when all of these hold:

- stdin is a TTY
- stdout is a TTY
- the `CI` environment variable is not set
- the `DRSKILL_NO_INTERACTIVE` environment variable is not set

Otherwise it prints one line, "review is interactive; no TTY detected. Use `drskill scan` for the report or `drskill ack <id>` to record decisions.", and exits 1.

## The loop

```
drskill review [--global] [--harness <id>]
```

`review` runs the scan pipeline once and iterates the active findings in report order, the same order the first spec defines. For each finding it prints the full evidence, exactly as `drskill show` renders it, plus a progress line such as "3 of 9" and the action bar.

Actions are single keypresses, read raw from the terminal with no Enter needed:

| key | action |
|---|---|
| `a` | ack the finding, routed by the first spec's rules; the destination ledger is shown |
| `n` | prompt for a one line note, then ack with the note, routed the same way |
| `f` | queue the finding's fix commands for the exit summary, then move to the next finding |
| `s` | skip, leaving the finding undecided |
| `q` | quit; remaining findings stay undecided |

Any other key reprints the action bar. Acks are written to the ledger immediately on each keypress, so quitting midway loses nothing.

## The exit summary

When the loop ends, by finishing or by `q`, review prints:

- What was acked and into which ledger.
- The queued fix commands, as one copy and paste block.
- How many findings remain undecided.

On macOS and Linux the fix command block is also placed on the clipboard, best effort: `pbcopy` on macOS, `xclip` or `xsel` on Linux, silently skipped when none is present. drskill still executes nothing against the user's skills. The only files it writes are its own ledgers and its own state. Review marks the findings it displayed as seen, the same way a scan does.

## Out of scope

- Executing fix commands. The read only identity holds: drskill never installs, edits, or deletes a skill.
- Full screen interfaces, panes, or mouse support. Findings scroll past like the report does. No new dependencies.
- A back action, bulk actions inside the loop, or editing existing acks. The recap table and `ack --all` forms cover bulk decisions.

## Implementation notes

- Single key input uses `termios` and `tty` from the standard library, restored on exit even after an exception. Windows is out of scope for v0.2; on Windows the guard message points at `scan` and `ack`.
- The keypress reader sits behind a small injectable interface, so tests drive the loop with scripted key sequences.
- Rendering reuses the `show` renderer from the first spec. Review adds only the progress line, the action bar, and the summary.

## Testing

- Guard: fake non TTY streams and each environment variable, asserting the one line refusal and exit code. No pipeline run happens when the guard fails.
- Scripted sessions: a fixture world with a global-only finding and a project finding, driven by key sequences. `a` writes to the right ledger per finding, `n` records the note, `f` queues the commands and the summary prints them, `s` leaves the finding active on the next scan, `q` stops without touching the rest.
- Progress preservation: quit after one ack, rescan, and the acked finding stays silent while the rest remain.
- Terminal state: the termios settings are restored after a normal run and after an injected exception.
