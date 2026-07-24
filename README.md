### `drskill` Spots Context Issues

`drskill` is `brew doctor` for your agent's loadout. Coding agents load Skills and connect to MCP servers before you type a word. `drskill` looks at every agent on your machine or in your repo, works out exactly which skills and which servers each one loads, and checks the whole set for problems.

On the skill side it finds:

1. Skills that shadow each other
2. Skills loaded twice
3. Duplicate or near-duplicate skills
4. Skills that break the SKILL.md spec
5. Broken symlinks
6. Drift against your lockfile
7. Skills that burn too many tokens

On the MCP side it finds:

1. The same server configured twice with drifted settings
2. Secrets sitting in a committable config file
3. Unpinned server packages that run whatever publishes next
4. Server commands that no longer exist
5. Tools whose descriptions collide with each other or with a skill
6. Servers that quietly change their tools after you approved them
7. Tool text carrying hidden instructions, credential paths, or steering toward or away from other tools

The last three need `drskill` to connect to the servers, which it does only when you ask.

Every problem it reports ends in a command: a fix command or a command to acknowledge the problem and move on. `drskill` reads your files and never installs, edits, or deletes a skill. It makes zero calls to an LLM unless you opt in with `scan --deep`, and it never launches or connects to an MCP server unless you opt in with `scan --mcp-connect`.

Use `drskill` to:

- Learn why an agent reaches for the wrong skill or tool, e.g. two descriptions overlap so a router cannot tell them apart
- Catch config risks before they ship, e.g. a secret in a committed file or an unpinned server package
- Notice when your loadout changes (without you doing anything), e.g. a skill that drifted from its lockfile or a server that rewrote a tool description
- Write skill and tool descriptions that do not clash with other libraries

## Install

```
uv tool install drskill
```

This installs everything, including the model-judged deep checks and the MCP server connection support.

For a minimal install, e.g. in CI, where neither is used, install the core package instead:

```
uv tool install drskill-core
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

Acknowledge (`ack`) a finding so it stops showing up until the skill's content changes. There are four forms:

```
drskill ack fe5b                     # one finding, by the id shown in the report
drskill ack near-duplicate docx-report documentation-writer   # one finding, named in full
drskill ack injection-egress         # every finding of one check
drskill ack --all                    # every active finding
```

Walk the findings one at a time and decide each with one keypress:

```
drskill review
```

`review` shows each finding with its full evidence and takes single-key actions: 

- `a` acks it, 
- `n` acks it with a note, 
- `f` queues its fix commands for a copy and paste block at the end, 
- `s` skips it, 
- and `q` quits. 

Each ack is written the moment you press the key, to the same ledger the `ack` command would pick: the project's `drskill.toml`, or `~/.drskill.toml` when the finding involves only machine-level skills. 

Quitting midway loses nothing, and the exit summary lists what was acked into which ledger. `review` only runs in a real terminal. When stdin or stdout is not a TTY, or `CI` or `DRSKILL_NO_INTERACTIVE` is set, it prints one line pointing at `scan` and `ack` and exits. `scan` itself never prompts, so a script or an agent calling drskill can never get stuck at a prompt.

Print the full evidence for a finding, or for a whole check class:

```
drskill show fe5b
drskill show injection-egress
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
drskill scan --harness claude-code
```

An unknown harness id is an error that names the valid ids. Harnesses that are detected but load no skills are hidden from the tables by default; a closing line names them, and `--all` shows them.

Run in CI, where any unacknowledged warning should fail the build:

```
drskill scan --ci
```

### Example scans

A few scans you might run:

Deeply check one agent's skills and MCPs. This scopes the scan to Claude Code and uses the model to judge which overlap warnings are real:

```
drskill scan --mcp-connect --harness claude-code --deep
```

Do a full audit before a Skill or MCP release. This judges skill overlaps and connects to every MCP server to read its tools:

```
drskill scan --deep --mcp-connect
```

Judge everything in one pass, with no cap on model calls:

```
drskill scan --deep --max-calls all
```

Gate a pull request. This connects to servers, judges overlaps, and fails the build on any unacknowledged warning:

```
drskill scan --deep --mcp-connect --ci
```

## Audit your usage

`drskill scan` looks at the loadout as configured. `drskill audit` looks at how you actually used it. It reads the local session traces that Claude Code, Codex, Pi, and Copilot already write to disk, and ranks which skills and MCP tools actually got invoked. `drskill audit <name>` drills into one skill or tool and shows the queries behind each invocation, and the agent's reasoning right before it, on harnesses that record reasoning.

Run it in a project to see that project's usage:

```
drskill audit
```

```
claude-code  coverage: 2026-07-20 to 2026-07-24 · 6 sessions · 57 invocations
name                        kind   uses  share  sessions  last used
superpowers:brainstorming  skill  14    25%    6         2026-07-23
superpowers:writing-plans  skill  8     14%    5         2026-07-23
plain-writing              skill  6     11%    5         2026-07-23
```

Widen to every project on the machine, and look at the last 30 days only:

```
drskill audit --global --since 30d
```

Drill into one skill to see the queries that led to it:

```
drskill audit overturemaps
```

```
overturemaps (skill)  codex 1
  2026-07-12 17:56  codex  ~/project
    query: I need to find coffee shops near a set of addresses…
    trace: ~/.codex/sessions/2026/07/12/rollout-2026-07-12T10-54-06.jsonl
```

A skill name and an MCP tool name can collide, so `drskill audit <name>` also takes the form `server:tool` to say which one you mean:

```
drskill audit pencil:get_screenshot --global
```

A few things to know about the numbers:

- On Codex and Pi, a skill count comes from seeing the agent read that skill's SKILL.md file, not from an explicit invocation event. These rows carry a `~` marker and the report explains it.
- Codex encrypts its reasoning, so audit cannot show reasoning for Codex invocations.
- Copilot records neither reasoning nor the structured arguments of a tool call, so its drill-downs are thinner than the other harnesses.
- Each harness keeps traces for a different length of time, so a raw count comparison across harnesses can mislead. The cross-harness rollup at the bottom of the report ranks by invocations per week within each harness's own coverage window instead, and says so when the windows differ a lot.

Audit only reads trace files. It writes nothing to the ledger, creates no findings, and has no effect on `--ci`.

Parsing every trace on every run would be slow, so audit caches what it extracts from each trace file at `~/.drskill/cache/audit/`. This cache is machine state and is never committed, because it holds the full text of the user messages that preceded each invocation, plus 200-character reasoning excerpts. The same text already exists in the agent trace files it was read from. `drskill cache prune` clears entries for trace files that no longer exist.

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
| `description-overlap` | warning | Two or more descriptions are similar enough that a router could confuse them. The finding names the cluster and the trigger phrases they share. Threshold `description_overlap`. |
| `missing-activation` | warning | A description never states when the skill should trigger, e.g. no "when", "trigger", or "if the user" phrasing. |
| `generic-description` | warning | A description has fewer distinctive words than `generic_min_distinct_tokens`, e.g. "Helps with various tasks." |
| `opposing-imperatives` | warning | Two skills give opposite orders about the same action, e.g. "Always use tabs" against "Never use tabs". Deliberately strict matching, so paraphrased conflicts are not caught. |
| `injection-unicode` | error | Skill text or a bundled file contains bidirectional control characters or zero-width characters. These can hide instructions from a human reviewer. |
| `injection-credential-read` | error | A bundled script references credential paths such as `~/.ssh`, `~/.aws`, or private key files. Reads of `.env` alone downgrade to a warning. |
| `injection-override` | warning | Skill text contains instruction-override phrasing, e.g. "ignore all previous instructions" or "without informing the user". |
| `injection-mandatory-script` | warning | The skill demands that its own bundled script runs as a required first step, e.g. "you must first run scripts/setup.sh". |
| `injection-egress` | warning | A bundled script calls the network, e.g. `curl` or `requests.post`. The finding quotes each call so you can check the destination. |
| `injection-encoded-blob` | warning | Skill text or a bundled file contains a long base64 or hex run that a reviewer cannot read. |
| `injection-remote-fetch` | warning | Skill text tells the agent to fetch remote content and act on it, e.g. `curl` piped to a shell or "download X and follow the instructions". |
| `mcp-config-invalid` | error | An MCP config file exists but does not parse. |
| `mcp-shadowed-server` | warning | One harness configures the same server name in project and user scope with different settings. The message names the winner. |
| `mcp-diverged-server` | warning | The same server name is configured differently across harnesses. The evidence lists the differing fields. |
| `mcp-secret-in-config` | error in project files, warning in user files | An MCP env block holds a credential-shaped literal value. Evidence names the variable, never the value. |
| `mcp-unpinned-server` | warning | A server runs an unpinned package, e.g. `npx -y pkg` or `pkg@latest`. Whatever publishes next runs next. |
| `mcp-insecure-url` | warning | A remote MCP server uses plaintext `http://`. Localhost is excluded. |
| `mcp-dead-server` | error | A stdio server's command is not on PATH or its absolute path does not exist. |
| `mcp-connect-failed` | warning | A `--mcp-connect` handshake to a server did not connect, timed out, or errored. |
| `mcp-tool-collision` | warning | Two servers expose the same tool name into one harness's set. Which one the agent gets is client dependent. |
| `mcp-tools-unreviewed` | note on first sight, warning on change | A server's enumerated tool set. On first sight it is a note asking you to record an approved baseline. If the server later changes a tool's description, it becomes a warning. |
| `mcp-tool-poisoning` | error for hidden-Unicode and credential-path hits, warning otherwise | Scans tool names, descriptions, and schema doc strings for injection surfaces: hidden instructions, credential paths, invisible Unicode, encoded blobs, remote-fetch directives, and text that steers the agent toward or away from other tools. Runs from committed snapshots, so the whole team gets findings after one person runs `--mcp-connect`. |

## Deep checks

The description-overlap check compares text, so some of its warnings are false alarms. `drskill scan --deep` sends each flagged pair of skills to a language model, which judges whether the two skills are distinct, whether their descriptions collide, or whether their scopes genuinely overlap. Deep mode is included in the standard install; only the minimal `drskill-core` install leaves it out. The only other requirement is a provider API key, e.g. `ANTHROPIC_API_KEY`. drskill sends only skill names and descriptions to the model, and it sends nothing at all unless you pass `--deep`.

The key comes from your environment. To set it once per machine, put it in `~/.drskill/env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

`drskill` reads this file before a deep run and loads any variable your shell has not already set. The shell always wins. `drskill` never writes a key, and it never reads an env file from inside a project, because a scanned repo is untrusted content.

The judge model is set in the ledger and defaults to a current Anthropic model:

```toml
[deep]
model = "anthropic/claude-haiku-4-5"
```

The model is a LiteLLM model id, so any provider LiteLLM supports works. To use an OpenAI model, set the id and put `OPENAI_API_KEY` in your environment:

```toml
[deep]
model = "openai/gpt-5.6-luna"
```

The provider is read from the id, so the only change is the model line and the matching key. Everything else, the cache, the budget, and the checks, is the same.

Verdicts are stored in `.drskill/cache/`, one small JSON file per judged pair. Commit this directory. Every scan reads it, with or without `--deep`, so one person runs the judgments and every teammate and CI run gets the verdicts for free. A verdict lasts until either description changes, and then the pair is judged again.

The cache carries the same trust as the ack ledger. Neither file is signed, so anyone who can commit to the repo can silence a warning through either one. Review a change to `.drskill/cache/` the way you review a change to `drskill.toml`.

Each `--deep` run makes at most 25 model calls. Raise or lower the budget with `--max-calls`, or pass `--max-calls all` to judge every flagged pair in one run. When a budget runs out, the report says how many pairs are still unjudged.

When every pair in an overlap cluster is judged distinct, the warning becomes a note. The note still prints, so the model's decision stays on the record, but it does not fail `--ci` and needs no ack. A skill with an unacknowledged injection finding never earns this downgrade. Its pairs are still judged and the verdicts print as evidence, but the warning stays a warning, because a skill suspected of prompt injection does not get to talk its way out of an overlap warning.

When the judge classes a pair as a description collision, the same run also proposes a fix. A second model call rewrites one of the two descriptions, and the finding shows the proposal as a diff: the current description on a minus line, the proposed one on a plus line, with the model's reason for picking that skill. The proposal is model text headed for your skill file, so read it before pasting. drskill never edits the file itself. A rewrite costs one extra call from the same `--max-calls` budget, and a proposal that failed to generate is retried at the start of the next `--deep` run. Once you apply a rewrite, the description has changed, so the next `--deep` run judges the pair fresh, and a good rewrite comes back distinct.

Two commands manage the cache. `drskill cache stats` prints entry counts by verdict, by model, and the age range. `drskill cache prune` deletes verdict entries and tool snapshots that no longer match any configured skill pair or server.

## MCP servers

Skills are half of an agent's loadout. MCP servers are the other half, and each harness configures them in its own file: `.mcp.json` and `~/.claude.json` for Claude Code, `.cursor/mcp.json` for Cursor, `.vscode/mcp.json` for VS Code, `~/.codex/config.toml` for Codex, `.gemini/settings.json` for Gemini CLI, and `claude_desktop_config.json` for Claude Desktop. drskill reads all of them on every scan. It only reads. Nothing is launched, and no server is connected to.

The `mcp-` checks in the table above cover what the config files alone can show: the same server configured twice with drifted settings, credential-shaped values sitting in a committable file, unpinned `npx` packages, plaintext remote URLs, and commands that no longer exist. A `?` after a harness name on an mcp finding means drskill has not verified that harness's config format against its docs.

See every configured server in one table:

```
drskill list --mcp
```

### Connecting to servers

The checks above read config files. To see what tools a server actually exposes, drskill has to ask the server:

```
drskill scan --mcp-connect
```

This connects to every configured server, runs the MCP handshake, and reads its tool list. drskill only enumerates. It never calls a tool, and it never reads a server's resources or prompts. Each server gets 15 seconds, and one that hangs is killed. A server that fails to connect becomes an `mcp-connect-failed` warning, and the scan moves on. Connecting needs the full install, since it uses the MCP SDK; a minimal `drskill-core` install leaves it out.

Each successful handshake writes a snapshot of the server's tools into `.drskill/cache/mcp-tools/`. The snapshot holds tool names, descriptions, and token counts. It holds no secret. Commit this directory. Every later scan reads the snapshots, so tool findings, the token bill, and the conflict checks work for the whole team without anyone connecting again, labeled "as of" the snapshot date.

Once tools are known, three things happen. Their descriptions flow through the same `description-overlap` and deep checks as skills, so a tool that collides with another tool or with a skill is flagged. The scan header gains the context bill: the size of the largest harness's starting context, split between its skill catalog and its MCP tool definitions.

And `mcp-tools-unreviewed` handles the tool descriptions themselves. A tool description is text the server writes, not you, and the agent loads it as instructions, so a server you trust can quietly rewrite it later. The first time drskill sees a server's tools it prints a note asking you to record them as an approved baseline. Acking saves that exact set. If the server later changes a tool's description or schema, the note becomes a warning that fails `--ci`, and the warning names each changed tool and shows its old text next to its new text, so you find out that a server changed what it tells your agent after you trusted it.

Snapshots fingerprint schema text as well as descriptions, and that coverage has grown over time. If you upgrade drskill and it now fingerprints more of a server's text than it did when you approved it, an unchanged server does not come back as a rug-pull warning. You get a one-time note instead, asking you to re-ack once to extend your approved baseline to the new fingerprint.

`mcp-tool-poisoning` reads the same committed snapshots and scans tool names, descriptions, and schema doc strings for injection surfaces: hidden instructions, credential paths, invisible Unicode, encoded blobs, remote-fetch directives, and text that steers the agent toward or away from other tools. Like the other MCP checks it is static and reads only what was captured at connect time, so the whole team gets findings the moment one person runs `--mcp-connect`, with no reconnect required. A server that legitimately manages credentials, e.g. an AWS or Kubernetes tool whose parameter docs name `~/.aws/credentials`, will trip the credential-path error on first connect. That is the check working as designed: read the quoted text, and if the mention is expected, `drskill ack mcp-tool-poisoning <server>` records your decision and the error stays silent until that tool's text changes.

## The ledger

`drskill.toml` sits at the root of your repo and should be committed. It holds your budgets, your thresholds, and your decisions. When you run `drskill ack`, it appends an entry to the end of the file and touches nothing else, so your comments and formatting are preserved. An entry looks like this:

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

Acks are scope aware. When a finding involves only machine-level skills, e.g. a vendored skill under your home directory that has nothing to do with the current repo, `drskill ack` writes the ack to `~/.drskill.toml` and says so. Every project scan honors acks from both ledgers, so you decide once per machine instead of once per repo. When any project skill is involved, the ack goes to the project's committed `drskill.toml` as before. Two flags override the routing: `--local` forces the project ledger, and `--global-ack` forces the machine ledger.

## Reading the report

Findings print errors first, then warnings. Inside each section the order is: findings you have not seen before, then findings on skills you installed, then findings on harness-vendored skills, which carry a `[system skill]` label. A finding you have not seen carries a `new` tag, and the summary line counts them. The memory behind the `new` tag lives in `~/.drskill/state/`, one small file per project. It only records what the report has shown you; it is not the ledger, and `--json` runs never touch it, so an agent polling `drskill` does not clear your markers. When a finding affects every detected harness, the harness line collapses to a count, e.g. "all 7 harnesses". Checks that flag description quality report one finding listing every offending skill, so three skills with the same problem are one entry and one ack.

The `source` column in `list` shows where a skill came from: `skills-lock` for skills named in a project's `skills-lock.json`, `gh-skill` for skills with `gh skill` provenance in their frontmatter, and `linked` for skills that live in or link into a `.agents/skills` store. The `linked` label means an installer arranged the layout; `drskill` does not guess which one. `unmanaged` means a plain directory with no known manager.

`list` shows a harness's whole loadout in one table: its skills and its MCP servers. Each row has a `kind` (`skill`, `mcp server`, or `mcp tool`) and a `suite`. A configured server shows as one `mcp server` row until you run `--mcp-connect`; after that it expands into one `mcp tool` row per tool the server exposes. The rows are sorted by suite, so a suite reads as a block: all your superpowers skills together, then the rest, then each server and its tools together.

The `suite` column names where a row came from. For a skill it is the plugin or repo. `drskill` recovers a plugin suite by matching the skill's content against the plugin caches on disk, so a plugin skill copied into a shared store is still recognized. For a skill that a lockfile tracks, the suite is the lockfile source, the same origin the `source` column records. For an MCP tool the suite is the server that exposes it. A skill with neither a plugin match nor a lockfile source shows a blank suite, because `drskill` does not guess a suite from a path or a bare name.

## Known limitations

Claude Code skills bundled inside plugins are not scanned yet. `drskill` only walks the plain `.claude/skills` directories described in the harness table; it does not look inside installed plugin packages.

`skills-lock.json` hash verification is self-calibrating. Upstream `npx skills` computes its own content hashes, and `drskill` cannot always reproduce them exactly. If none of the hashes in a lockfile match what `drskill` computes, it will not accuse every skill of drift; instead it prints one warning saying the hashes could not be verified against that lockfile. Per-skill drift warnings only appear once `drskill` has confirmed, by matching at least one hash, that its hashing algorithm agrees with that lockfile's producer.

Harness rules are verified in two parts, because they have two different jobs. Paths verification covers which directories a harness reads and whether it searches them recursively. Precedence verification covers which copy wins when two skills share a name. Claude Code, Pi, Gemini CLI, Codex, and Cline are verified on both. Cursor is verified on paths only, since its docs do not say which copy wins a collision. Copilot is unverified on both, since its docs do not confirm recursion and the CLI is closed source. About 65 further harnesses are vendored from the `vercel-labs/skills` project and are unverified on both.

A finding only inherits the uncertainty it actually depends on. Shadowing and double-load findings depend on precedence; every other finding depends only on paths. When a harness in a finding's list is unverified for the part that finding depends on, its name carries a `?` suffix, and the report ends with one legend line explaining it. A finding with no `?` rests entirely on verified rules.

Token counts are approximate. `drskill` counts tokens with `tiktoken`'s `o200k_base` encoding, which is a reasonable estimate but will not match every harness's actual tokenizer or catalog rendering exactly.

The seven injection checks flag surfaces; they do not verify intent. Static analysis cannot prove a skill benign or hostile, so every injection finding quotes the exact lines it judged and leaves the verdict to you. A clean scan is not a security guarantee, and a finding is not an accusation. Bundled files that are binary or larger than 1 MiB are recorded but not content scanned, and the report says so when that happens. A bundled file counts as a script when it has a script extension or a shebang line; everything else is scanned as prose, so the script-only checks (egress, credential reads) do not look inside files disguised as plain text.

The four description and instruction checks are heuristics. Their thresholds are tuned against real public skill sets to stay quiet on well-written skills, and every finding can be acknowledged, but they will miss paraphrased conflicts and will flag some judgment calls. The thresholds live in `drskill.toml`:

```toml
[thresholds]
near_duplicate = 0.85
description_overlap = 0.6
generic_min_distinct_tokens = 2
```
