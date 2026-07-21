# Tier 3 injection surface checks

Date: 2026-07-20
Status: approved
Parent documents: `initial_design_doc.md` (section 5, Tier 3), `docs/superpowers/specs/2026-07-19-drskill-v0.1-design.md`, `docs/superpowers/specs/2026-07-20-tier2-heuristics-design.md`

This is cycle 2 of 3 for the v0.2 release. Cycle 1 was the Tier 2 heuristics and cycle 3 is the `explain` command. One 0.2.0 release ships after all three.

## Why

Skills are instructions plus optional bundled files, and installed skills are unverified. The known attack patterns are documented. A skill can tell the agent to ignore its other instructions. It can demand that the agent runs a bundled script before anything else. A bundled script can send data over the network or read credential files. Text can hide payloads in encoded blobs or in invisible Unicode characters. Instructions can tell the agent to fetch remote content mid task and follow it.

Tier 3 flags these surfaces statically. It never claims to verify anything, because static analysis cannot prove a skill benign. Every finding quotes the suspicious lines with their paths, so the user can judge the evidence themselves. There is no LLM call, no network access, and nothing is ever executed.

The noise risk from the Tier 2 cycle applies here too. The lexicons and thresholds get tuned against real corpora before merge, and every finding is ack-able.

## Bundled files

Until now the tool only read SKILL.md. Tier 3 needs the files a skill ships with, such as `scripts/` and `references/`. Attackers do not follow directory conventions, so collection covers the whole skill directory.

A new model in `models.py`:

```python
class BundledFile(BaseModel):
    relpath: str        # posix style, relative to the skill directory
    size: int
    content_hash: str   # sha256 of the raw bytes
    is_text: bool       # no null byte in the first 8 KiB
    oversize: bool      # larger than the 1 MiB scan cap
```

`Contributor` gains `bundled_files: list[BundledFile]`, defaulting to empty.

Collection happens in `build_world`, once per contributor, and only when the skill file is named SKILL.md. A bare `.md` skill has no directory of its own, so it gets no bundled files. Its text is still scanned by the prose checks. The walk uses the symlink loop guard that discovery already has, covers the whole realpath skill directory recursively, and takes every file except SKILL.md itself. Unreadable files go into the existing `world.unreadable` list. Contributors are already deduplicated by realpath, so a skill symlinked into five agent directories is collected once.

The hash is over raw bytes with no normalization, because bundled files have no provenance frontmatter to strip. Collection computes only metadata. File text is read lazily at check time through a shared scan view, capped at 1 MiB per file. Binary and oversize files are recorded but never content scanned, and the report states this when it happens.

## The scan view

The checks live in a new module, `checks/injection.py`. A module level helper builds one lazily cached scan view per contributor. The view is a list of sources, where each source has a label and its lines. The sources are the SKILL.md text, meaning frontmatter plus body, and each bundled text file under the cap. Each file is read from disk once per scan, no matter how many checks consult it.

Each source is classed as a script or as prose:

- A script has one of the extensions `.py`, `.sh`, `.bash`, `.zsh`, `.js`, `.mjs`, `.ts`, `.rb`, `.pl`, `.ps1`, or its first line starts with `#!`.
- Everything else textual is prose. This includes reference documents.

The checks stay independent functions in the registry. They share the view the same way the Tier 2 checks share the `text.py` utilities.

## The seven checks

One check id per surface, so the bare check id ack form silences exactly one class. All seven are on by default.

| check id | severity | scans | fires on |
|---|---|---|---|
| `injection-unicode` | error | all text | bidi control characters (U+202A to U+202E, U+2066 to U+2069), zero width space (U+200B), and a byte order mark that is not at the start of a file (U+FEFF) |
| `injection-credential-read` | error | scripts | path references to `~/.ssh`, `id_rsa`, `id_ed25519`, `.pem` and `.key` private key files, `~/.aws`, `~/.config/gcloud`, `.netrc`, `~/.kube/config`, and browser profile paths such as `.mozilla/firefox` and the Chrome profile directories |
| `injection-override` | warning | prose and SKILL.md | instruction override phrasing, e.g. "ignore previous instructions", "disregard your rules", "do not tell the user", "without informing the user" |
| `injection-mandatory-script` | warning | SKILL.md body | a mandatory first step framing, e.g. "you must first run" or "before anything else, run", combined with a path that matches one of the skill's own bundled files |
| `injection-egress` | warning | scripts | network tokens per language, e.g. `curl`, `wget`, `nc`, `Invoke-WebRequest`, `requests.`, `urllib`, `httpx`, `socket.`, `fetch(`, `http.request`, `Net::HTTP` |
| `injection-encoded-blob` | warning | all text | a run of base64 characters of 120 or more, or a run of hex characters of 128 or more |
| `injection-remote-fetch` | warning | prose and SKILL.md | an instruction to fetch remote content and execute it or follow it, e.g. a URL plus "run", "execute", or "follow the instructions", or `curl` piped to a shell inside instruction text |

Notes on the choices:

- `injection-unicode` excludes the zero width joiner and non joiner outright. Emoji sequences and several writing systems use them, so flagging them would drown the signal. Bidi controls and zero width spaces have no business in a skill file.
- `injection-credential-read` downgrades to a warning when the matched path is `.env`, because projects legitimately read their own `.env` files. The other paths are errors.
- `injection-mandatory-script` needs both parts. The mandatory framing alone does not fire, and a plain pointer such as "run scripts/foo.py to convert the file" does not fire. This is the documented SkillJect pattern, so the discriminator is the demand that the script runs first or always.
- `injection-encoded-blob` sets the hex threshold above 64 characters so sha256 hashes in lockfiles and documents stay quiet.
- `injection-egress` will be common in legitimate skills that call APIs. The hermes corpus decides whether the lexicon needs narrowing before merge. The evidence quoting is what keeps the finding useful either way, because the user sees the exact call and its target.

Every lexicon and threshold is a module constant, not a ledger key. This matches the Tier 2 stance on the activation lexicon. The ack ledger is the escape hatch, and corpus tuning has final say on the contents before merge.

## Findings

One finding per skill per check, aggregating all hits across that skill's files. The message leads with the standard 4 hex id and quotes the evidence. Each hit shows the file path relative to the skill directory, the line number, and the line itself, trimmed to the report width, rich escaped, with invisible characters rendered as escape codes. `injection-unicode` also names the codepoints it found. At most 3 hits are quoted per finding, followed by a count of the remaining hits. A finding never asserts a surface without showing a line.

Fingerprints follow the established basis rule. The fingerprint hashes the full contents of the files that contain hits, raw bytes for bundled files and normalized content for SKILL.md, qualified with the skill name. An ack therefore survives edits to files without hits and resurfaces when a hit file changes or a new file starts hitting.

Error findings recommend removal and end with concrete commands. When the skill is installer managed the command is `npx skills remove <name>`. Otherwise it is a shell quoted `rm -r` of the skill directory. The ack line is printed as well, since every finding is ack-able. Warning findings end with the ack line.

## Report and CLI

Nothing structural changes. Tier 3 findings flow through the existing severity sections, finding ids, recap table, batch ack forms, and `--ci` exit codes. One addition. When any bundled file was skipped, the report prints one aggregate line after the findings, e.g. "12 bundled files not content scanned (11 binary, 1 over 1 MiB) across 3 skills". The caps are stated, never silent.

## Corpus tuning

`scripts/corpus.py` gains a Tier 3 sheet. For each corpus it prints every injection finding with its full quoted evidence, plus a count per check. The corpora are the same three from the Tier 2 cycle. The hermes-agent corpus is the noise gate, because its 179 skills include real bundled scripts. anthropics/skills and vercel-labs/agent-skills sanity check the prose checks.

We hand review the sheets before merge. False positives get fixed in the lexicons, not shipped. Clear verdicts freeze into conformance cases, with a LICENSE-NOTE.md in the case directory whenever skill text is copied in.

## Testing

- Unit tests per check, each with a firing case and a near miss that must not fire. The near misses include a plain script pointer for `injection-mandatory-script`, a sha256 hash for `injection-encoded-blob`, and an emoji joiner sequence for `injection-unicode`.
- Infrastructure tests for the collection and the scan view. These cover the binary sniff, the size cap, a symlink loop inside a skill directory, an unreadable bundled file, and a bare `.md` skill with no directory.
- Conformance. A synthetic hostile skill fixture, written by us and inert, expects all seven checks. The existing clean cases get forbid entries for all seven.
- The merge gate from the earlier cycles. After tuning, `uv run drskill scan` runs on the real loadout on this machine, and false positives get fixed before merge.

## Out of scope for this cycle

- The `explain` command, which is cycle 3.
- Any execution, sandboxing, or verification of bundled scripts.
- Any LLM, embedding, or network dependency.
- User tunable lexicons or thresholds for the injection checks.
- Token accounting for bundled files.
