# The deep foundation

Date: 2026-07-21
Status: approved
Parent documents: `initial_design_doc.md`, `docs/superpowers/specs/2026-07-19-drskill-v0.1-design.md`, `docs/superpowers/specs/2026-07-20-tier2-heuristics-design.md`, `docs/superpowers/specs/2026-07-20-tier3-injection-design.md`, `docs/superpowers/specs/2026-07-20-report-triage-design.md`

This is the first of four planned cycles for the v0.3 release. The later cycles are DescriptionRewrite diffs, GEPA compilation of the prompt programs, and the model judged explain command. The `share` feature is not part of this release.

## Why

The description-overlap check compares text. It cannot tell a real scope collision from two skills that happen to share vocabulary, so some of its warnings are false alarms the user must ack by hand. This cycle adds the first LLM tier. A model reads each flagged pair and judges whether the two skills are actually distinct, whether their descriptions collide, or whether their scopes overlap. The verdicts are cached in a committed file, so a team pays for each judgment once. A pair the model judges distinct stops failing CI, which is the main value of paying for the call.

This cycle ships the whole pipeline with hand written prompts. A later cycle compiles the prompts with GEPA and ships the compiled artifacts. That upgrade changes no interface in this spec.

## Install and configuration

The `[deep]` extras group holds the one new dependency, `dspy`, which brings LiteLLM with it. Nothing in the default install changes. `dspy` is imported lazily. A scan without `--deep` never loads it, which keeps the existing CLI startup rule.

The ledger gains a `[deep]` section with one key:

```toml
[deep]
model = "anthropic/claude-haiku-4-5"
```

The value is a LiteLLM model id. The default, used when the section is absent, is `anthropic/claude-haiku-4-5`. The setting is project local, like budgets and thresholds, because the cache is a team artifact and the verdicts in it should come from a model the team chose together. Global mode reads the machine ledger's `[deep]` section.

API keys come from the environment, through the standard LiteLLM variables such as `ANTHROPIC_API_KEY`. For persistence, drskill follows the AWS credentials file pattern. Before a `--deep` run it reads `~/.drskill/env`, a plain KEY=value file the user creates and owns, and loads any variable the shell has not already set. The shell always wins over the file. drskill never writes a key anywhere, and it never reads an env file from a project directory, because a scanned repo is untrusted content and a repo-supplied variable such as a base URL override could redirect the user's key to an attacker. The missing-key error names the exact variable, mentions the env file, and links to the provider's key console. Two auth notes for the record. Anthropic's `ant auth login` OAuth profiles are a sanctioned keyless way to bill an API account, and wiring their short lived bearer tokens through LiteLLM is a possible follow-up, but it is out of scope this cycle. Consumer Claude subscription credentials are not an option at all. A Pro or Max plan covers Anthropic's own clients, and its terms do not permit third party tools to spend it through the API.

Two failure modes stop the run before any scan work:

- `--deep` without the extras installed prints one line naming `pip install 'drskill[deep]'` and exits 1.
- `--deep` without a usable key for the configured model prints one line naming the missing variable and exits 1.

## Pipeline flow

Every scan, deep or not, reads the verdict cache and applies cached verdicts to the report. Reading the cache is plain JSON and needs neither the extras nor a key. This is how one person's `--deep` run benefits every teammate and CI.

`--deep` adds the judging step. The flagged pairs are all unordered pairs of members within each description-overlap cluster. Pairs whose key is already in the cache are skipped. The remaining pairs are judged in a stable order, largest cluster first and then by skill name, so repeated runs under a budget make progress instead of rejudging the same prefix. The `--max-calls N` flag is a hard budget per run, default 25. When the budget truncates the work, the report says how many flagged pairs remain unjudged. Nothing is truncated silently.

## The ConflictJudge program

ConflictJudge is a DSPy signature with hand written instructions. Its inputs are the names and descriptions of the two skills. Its output has three fields:

- A class, one of `distinct`, `description_collision`, or `scope_overlap`.
- A one sentence rationale.
- For `distinct`, the distinguisher, meaning the difference that separates the two scopes. For the other two classes, a confusion example, meaning a query that could route to either skill.

Output is read through DSPy's typed prediction layer. A call that errors, times out, or returns output that does not parse is not cached. The pair's finding stays a plain warning with a note that the deep verdict is unavailable, and the scan continues. The run exits with its normal code.

## The verdict cache

The cache lives in `.drskill/cache/` inside the project, and the intent is that the team commits it. Global mode uses `~/.drskill/cache/`. Each verdict is one JSON file named by its key, so two teammates judging different pairs never conflict on merge.

The key is a sha256 over the normalized names and descriptions of the two skills, computed so the order of the pair does not matter. Normalization is the same content normalization the rest of drskill uses. The key covers only content. Editing either description changes the key, so the pair is judged again. Upgrading drskill or changing the configured model does not invalidate the cache. The entry records what judged it.

Each entry stores the class, the rationale, the distinguisher or confusion example, the model id, the drskill program version, and the date.

`drskill cache stats` prints entry counts grouped by class, by model, and by age. `drskill cache prune` deletes entries whose keys no longer match any currently flagged pair, and prints what it removed. Both commands work without the extras.

## Effect on the report

Verdicts change how the existing description-overlap findings print. The check logic, the finding fingerprints, and the ack semantics do not change, so existing acks stay valid.

- If every pair in a cluster has a cached `distinct` verdict, the cluster's warning becomes a short informational note, e.g. "overlap flagged, judged distinct by anthropic/claude-haiku-4-5, 2026-07-21". The note does not fail `--ci` and needs no ack. It still prints, so the downgrade is never invisible.
- Any other state keeps the warning. A `description_collision` or `scope_overlap` verdict adds the class, the rationale, and the confusion example to the finding's evidence. A pair with no verdict adds a note saying it is unjudged, or that its verdict was unavailable.

Model output is text from outside drskill. It is escaped for rich markup and passed through the report sanitizer, the same as skill text.

## Security stance

The descriptions sent to the judge are attacker controlled. A hostile skill could embed instructions aimed at the judge, hoping to earn a `distinct` verdict that quiets a deliberate collision. Three defenses apply:

- The judge prompt delimits the descriptions as data and instructs the model to treat them as text under analysis, not as instructions. This limits the attack but cannot eliminate it.
- A skill with any active Tier 3 injection finding is not eligible for the downgrade. Active means not acknowledged. An injection finding the user has acked reflects the user's own judgment, so it does not block the downgrade. For an ineligible skill, its pairs are still judged and the verdicts still print as evidence, but the warning stays a warning, with a line saying why. A skill that is currently suspected of injection does not get to talk its way out of an overlap warning.
- The downgrade is never invisible. The informational note keeps the model's decision on the record in every report.

The committed cache is trusted the same way the committed ack ledger is trusted. Neither file is signed, because the repo holds no secret to sign with, so anyone who can commit to the repo can silence a warning through either one. Review a change to `.drskill/cache/` the way you review a change to `drskill.toml`. A forged entry still leaves a visible note in every report, naming the model and date it claims.

## Testing

- The DSPy program sits behind a small injectable judge interface. Tests drive the pipeline with scripted verdicts and never make a network call.
- Cache round trip, key stability across pair order, key change on description edit, and prune behavior.
- Budget truncation prints the remaining count. The stable judging order holds across runs.
- Report states: full distinct cluster downgrades to a note and stops failing `--ci`, mixed verdicts keep the warning with evidence, unavailable verdicts keep the warning with the note.
- The no-extras error, the no-key error, and the injection ineligibility rule.
- A hostile description in a fixture renders escaped in the verdict evidence.
- Every test sets `DRSKILL_HOME`.
- The real machine gate: before merge, one real `--deep` run with a real key against the author's loadout and a corpus slice, with the verdict quality reviewed by hand. Misleading verdicts get fixed, not shipped.

## Out of scope

- DescriptionRewrite and its diffs.
- GEPA compilation, labeled corpora for optimization, and shipped compiled artifacts.
- The model judged explain command.
- `drskill share`.
- Judging any pairs other than description-overlap cluster members.
- Auto-applying anything. drskill still never edits a skill.
