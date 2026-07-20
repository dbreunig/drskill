### They Call Me Dr. Skill

drskill is `brew doctor` for your agent's skill loadout. It looks at every coding agent installed on your machine or configured in your repo, works out exactly which skills each one loads, and checks that set for problems: skills that shadow each other, skills loaded twice, duplicate or near-duplicate skills, skills that break the SKILL.md spec, broken symlinks, drift against your lockfile, and skills that burn too many tokens. Every problem it reports ends in a command: a fix command or a command to acknowledge the problem and move on. drskill only reads your files. It never installs, edits, or deletes a skill, and it makes zero calls to an LLM.

## Install

```
uv tool install drskill
```

## Quick start

Run a scan from the root of a project:

```
drskill scan
```

This detects every coding agent it can find, resolves each one's effective skill set, and prints a report grouped by severity. Each finding names the harnesses it affects and ends in a fix command or an ack command.

Write a starter ledger file with default budgets and thresholds:

```
drskill init
```

Acknowledge a finding so it stops showing up until the skill's content changes:

```
drskill ack near-duplicate docx-report documentation-writer
```

List every harness's effective skill set with token counts:

```
drskill list --tokens
```

Scan and also print each harness's skill table in one run:

```
drskill scan --detailed
```

Scope the scan to a single harness and see exactly what that harness sees:

```
drskill scan --harness pi
```

An unknown harness id is an error that names the valid ids. Harnesses that are detected but load no skills are hidden from the tables by default; a closing line names them, and `--all` shows them.

Run in CI, where any unacknowledged warning should fail the build:

```
drskill scan --ci
```

## Exit codes

| code | meaning |
|---|---|
| 0 | clean, or every finding is acknowledged |
| 1 | at least one error-level finding is active |
| 2 | only warnings are active, but `--ci` was passed |

Without `--ci`, warnings alone exit 0. This lets you run `drskill scan` locally without it failing your shell, while still failing CI on the same warnings.

## Checks

| check id | severity | fires when |
|---|---|---|
| `name-shadow` | warning | Two skills share a name in one harness's set and one shadows the other. The message names the winner and the rule that picked it. |
| `double-load` | error | One harness loads the same logical skill twice through two directories. |
| `exact-duplicate` | warning | Two contributors have equal normalized content hashes under different names or paths. |
| `near-duplicate` | warning | Jaccard similarity of MinHash signatures over word shingles is at or above the threshold. The default threshold is 0.85 and can be changed in the ledger. |
| `spec-name-mismatch` | error | Frontmatter `name` does not match the folder name. |
| `spec-missing-description` | error | The description is absent or empty. |
| `spec-description-too-long` | error | The description exceeds 1024 characters. |
| `spec-invalid-frontmatter` | error | The frontmatter does not parse as YAML. |
| `frontmatter-angle-brackets` | warning | Frontmatter values contain angle brackets, which the spec flags as an injection vector. |
| `broken-symlink` | error | A symlink in a skill directory points at nothing. |
| `lockfile-drift` | warning | A skill's content hash does not match its `skills-lock.json` entry. The message attributes the likely cause, e.g. a `gh skill update` or a hand edit, and does not call it corruption. |
| `budget-catalog-tokens` | warning | A harness's total catalog tokens exceed `[budget] catalog_tokens_max`. |
| `budget-body-tokens` | warning | A skill's body tokens exceed `[budget] body_tokens_warn`. |

## The ledger

`drskill.toml` sits at the root of your repo and should be committed. It holds your budgets, your thresholds, and your decisions. When you run `drskill ack`, it appends an entry like this:

```toml
[[ack]]
check = "near-duplicate"
skills = ["docx-report", "documentation-writer"]
fingerprint = "sha256:..."
note = "docx is output format specific; keeping both"
date = 2026-07-19
```

A finding's fingerprint is a hash of the check id plus the content of every skill involved. An ack silences a finding only while that fingerprint still matches. If you edit one of the skills named in the ack, its content hash changes, the fingerprint no longer matches, and the finding comes back on the next scan. This is deliberate. An ack means "this exact situation is fine," not "never check this pair again."

In global mode (`--global`), the ledger lives at `~/.drskill.toml` instead.

The `source` column in `list` shows where a skill came from: `skills-lock` for skills named in a project's `skills-lock.json`, `gh-skill` for skills with `gh skill` provenance in their frontmatter, and `linked` for skills that live in or link into a `.agents/skills` store. The `linked` label means an installer arranged the layout; drskill does not guess which one. `unmanaged` means a plain directory with no known manager.

## Known limitations

Comments in `drskill.toml` are lost when `drskill ack` rewrites the file. The file is parsed and re-written as data, and the writer does not carry comments forward. If you rely on comments to explain a budget or a threshold, keep that explanation in a separate note, not inline in the file.

Claude Code skills bundled inside plugins are not scanned yet. drskill only walks the plain `.claude/skills` directories described in the harness table; it does not look inside installed plugin packages.

`skills-lock.json` hash verification is self-calibrating. Upstream `npx skills` computes its own content hashes, and drskill cannot always reproduce them exactly. If none of the hashes in a lockfile match what drskill computes, it will not accuse every skill of drift; instead it prints one warning saying the hashes could not be verified against that lockfile. Per-skill drift warnings only appear once drskill has confirmed, by matching at least one hash, that its hashing algorithm agrees with that lockfile's producer.

Harness rules have three levels of confidence. Claude Code, Pi, and Gemini CLI are verified against their own docs or source. Codex, Cursor, and Copilot are best effort. Codex in particular does not shadow skills the way drskill's precedence model assumes, since Codex keeps both copies visible on a name collision instead of picking a winner, and that behavior does not fit the model yet. About 66 further harnesses are vendored from the `vercel-labs/skills` project and are also best effort; drskill detects them and reports their findings, but their search paths have not been independently confirmed against each harness's own documentation. Reports label every finding from an unverified harness "best effort" so you know how much to trust it.

Token counts are approximate. drskill counts tokens with `tiktoken`'s `o200k_base` encoding, which is a reasonable estimate but will not match every harness's actual tokenizer or catalog rendering exactly.
