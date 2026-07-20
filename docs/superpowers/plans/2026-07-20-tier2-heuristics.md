# Tier 2 Heuristic Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the four Tier-2 heuristic checks (description-overlap, missing-activation, generic-description, opposing-imperatives) with corpus-tuned thresholds.

**Architecture:** A new `text.py` utility module (tokenizer, stopwords, shingle vectors, cosine, shared phrases, pattern data) feeds a new `checks/heuristics.py` module registering four warning checks in the existing registry. A dev-only `scripts/corpus.py` clones three real corpora and emits review sheets used to tune the two new ledger thresholds. Spec: `docs/superpowers/specs/2026-07-20-tier2-heuristics-design.md`.

**Tech Stack:** Existing stack only (pure stdlib for the new algorithms; pytest via uv). No new dependencies.

## Global Constraints

- All four checks: severity `warning`, on by default, SKILL.md contributors only (match Tier-1 spec checks' `Path(c.id).name == "SKILL.md"` gate), ack-able through the existing ledger.
- No LLM, embedding, or network dependency in any check.
- Ledger `[thresholds]` gains `description_overlap` (provisional 0.6) and `generic_min_distinct_tokens` (provisional 2); corpus tuning (Task 5) has final say and updates `INIT_TEMPLATE` + spec.
- description-overlap excludes duplicate pairs by re-deriving the condition (equal content hashes, or MinHash estimate >= `thresholds.near_duplicate` using helpers imported from `checks/duplicates.py`) — never by reading another check's output.
- `scripts/corpus.py` is dev-only: lives in `scripts/` (outside `src/drskill`, so hatchling's `packages = ["src/drskill"]` already excludes it); clones into gitignored `.corpus/`.
- All dynamic text rendered through rich goes through `escape()`; skill names/paths in fix commands through `shlex.quote()` (existing conventions; heuristics messages include description excerpts — escape applies at render time automatically since report escapes `f.message`, so just build plain strings).
- Stage only named files when committing; never `git add -A` (`initial_design_doc.md` stays untracked).

---

### Task 1: text utilities

**Files:**
- Create: `src/drskill/text.py`
- Test: `tests/test_text.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used by Tasks 2-5): `tokenize(text: str) -> list[str]`, `STOPWORDS: frozenset[str]`, `content_tokens(text: str) -> list[str]`, `shingle_vector(text: str, k: int = 2) -> dict[str, int]`, `cosine(a: dict, b: dict) -> float`, `shared_phrases(texts: list[str], max_n: int = 3) -> list[str]`, `has_activation(text: str) -> bool`, `ACTIVATION_PATTERNS: tuple[str, ...]`, `GENERIC_VOCAB: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

`tests/test_text.py`:

```python
from drskill.text import (
    GENERIC_VOCAB,
    STOPWORDS,
    content_tokens,
    cosine,
    has_activation,
    shared_phrases,
    shingle_vector,
    tokenize,
)


def test_tokenize_lowercases_and_splits():
    assert tokenize("Use Git's rebase, then squash!") == ["use", "git's", "rebase", "then", "squash"]


def test_content_tokens_drop_stopwords():
    toks = content_tokens("Use this skill when the user asks to rebase with git")
    assert "rebase" in toks and "git" in toks
    assert "use" not in toks and "the" not in toks and "skill" not in toks


def test_shingle_vector_bigrams_and_counts():
    v = shingle_vector("rebase git rebase git")
    assert v["rebase git"] == 2
    assert v["git rebase"] == 1


def test_shingle_vector_short_text():
    assert shingle_vector("rebase") == {"rebase": 1}
    assert shingle_vector("the a of") == {}


def test_cosine_bounds():
    a = shingle_vector("write project documentation pages")
    assert cosine(a, a) == 1.0
    b = shingle_vector("cook pasta dinner tonight")
    assert cosine(a, b) == 0.0
    assert cosine(a, {}) == 0.0


def test_cosine_partial_overlap():
    a = shingle_vector("write project documentation pages carefully")
    b = shingle_vector("write project documentation summaries carefully")
    assert 0.0 < cosine(a, b) < 1.0


def test_shared_phrases_longest_first_no_substrings():
    texts = [
        "Use when writing project documentation pages",
        "Use when writing project documentation summaries",
    ]
    phrases = shared_phrases(texts)
    assert phrases[0] == "writing project documentation"
    assert "project documentation" not in phrases  # substring of a kept longer phrase


def test_shared_phrases_empty_when_nothing_common():
    assert shared_phrases(["rebase git commits", "cook pasta dinner"]) == []


def test_has_activation():
    assert has_activation("Use when the user asks for a Word document.")
    assert has_activation("Invoke for database migrations.")
    assert not has_activation("Formats source code files.")


def test_vocab_contents():
    assert "tasks" in GENERIC_VOCAB and "helps" in GENERIC_VOCAB
    assert "when" in STOPWORDS and "use" in STOPWORDS
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_text.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'drskill.text'`.

- [ ] **Step 3: Write `src/drskill/text.py`**

```python
"""Shared text heuristics: tokenizing, similarity, and pattern data.

Everything here is deterministic and dependency free. The Tier 2 checks and
the corpus tuning script build on these primitives.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")

STOPWORDS: frozenset[str] = frozenset(
    """a an and are as at be by for from has have if in into is it its of on or
    so such that the their then there these this to was will with you your i we
    when where how what which who whom whose why can could should would may
    might must do does did done being been am was were not no yes here
    use uses used using user users skill skills ask asks asked asking""".split()
)

# Matched against the raw lowercased description BEFORE stopword removal.
ACTIVATION_PATTERNS: tuple[str, ...] = (
    "use when",
    "use this when",
    "use this skill when",
    "use whenever",
    "when the user",
    "when you",
    "when a ",
    "when working",
    "trigger",
    "invoke",
    "for questions about",
    "if the user",
    "before ",
    "after ",
    "during ",
)

GENERIC_VOCAB: frozenset[str] = frozenset(
    """help helps assist assists task tasks various general support supports
    work works handle handles manage manages tool tools thing things stuff
    item items way ways""".split()
)


def tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def content_tokens(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS]


def shingle_vector(text: str, k: int = 2) -> dict[str, int]:
    toks = content_tokens(text)
    if not toks:
        return {}
    if len(toks) < k:
        grams = [" ".join(toks)]
    else:
        grams = [" ".join(toks[i : i + k]) for i in range(len(toks) - k + 1)]
    vec: dict[str, int] = {}
    for g in grams:
        vec[g] = vec.get(g, 0) + 1
    return vec


def cosine(a: dict[str, int], b: dict[str, int]) -> float:
    if not a or not b:
        return 0.0
    num = sum(count * b[key] for key, count in a.items() if key in b)
    den = (
        sum(v * v for v in a.values()) ** 0.5 * sum(v * v for v in b.values()) ** 0.5
    )
    return num / den if den else 0.0


def shared_phrases(texts: list[str], max_n: int = 3) -> list[str]:
    """Longest word n-grams (content tokens) common to every text, longest
    first, substrings of already-kept phrases dropped."""
    token_lists = [content_tokens(t) for t in texts]
    if not token_lists or any(not tl for tl in token_lists):
        return []
    kept: list[str] = []
    for n in range(max_n, 0, -1):
        gram_sets = []
        for tl in token_lists:
            if len(tl) < n:
                gram_sets.append(set())
                continue
            gram_sets.append({" ".join(tl[i : i + n]) for i in range(len(tl) - n + 1)})
        for phrase in sorted(set.intersection(*gram_sets)):
            if not any(phrase in longer for longer in kept):
                kept.append(phrase)
    return kept


def has_activation(text: str) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in ACTIVATION_PATTERNS)
```

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/test_text.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/text.py tests/test_text.py
git commit -m "feat: shared text heuristics module"
```

---

### Task 2: missing-activation and generic-description checks

**Files:**
- Create: `src/drskill/checks/heuristics.py`
- Modify: `src/drskill/ledger.py` (Thresholds), `src/drskill/checks/__init__.py` (run_all import list), `src/drskill/cli.py` (INIT_TEMPLATE)
- Test: `tests/test_checks_heuristics.py`
- Create: conformance cases `missing-activation/`, `generic-description/`

**Interfaces:**
- Consumes: `text.has_activation`, `text.content_tokens`, `text.GENERIC_VOCAB` (Task 1); `checks.check/make_finding`; `config.thresholds`.
- Produces: registered checks `missing-activation`, `generic-description`; `ledger.Thresholds.generic_min_distinct_tokens: int = 2` and `description_overlap: float = 0.6` (added now so one ledger change covers Tasks 2-3); helper `heuristics._skill_md(world)` reused by Tasks 3-4.

- [ ] **Step 1: Write the failing test**

`tests/test_checks_heuristics.py`:

```python
from pathlib import Path

from drskill.checks import run_all
from drskill.discovery import discover
from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.resolution import build_world


def world_from(proj, home):
    h = next(x for x in load_harnesses() if x.id == "claude-code")
    i, b = discover(h, proj, home)
    return build_world(i, {h.id: h}, b)


def write(proj, name, description, body="body"):
    d = proj / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    )


def by_check(findings, check_id):
    return [f for f in findings if f.check_id == check_id]


def test_missing_activation_fires_and_spares(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "no-cond", "Formats source code files.")
    write(proj, "with-cond", "Use when the user asks to reformat code.")
    findings = run_all(world_from(proj, home), Config())
    hits = by_check(findings, "missing-activation")
    assert [f.contributor_names for f in hits] == [["no-cond"]]
    assert hits[0].severity == "warning"


def test_missing_activation_skips_empty_description(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "empty-desc", "")
    findings = run_all(world_from(proj, home), Config())
    assert by_check(findings, "missing-activation") == []
    assert by_check(findings, "spec-missing-description")  # Tier 1 owns this


def test_generic_description(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "vague", "Helps with various tasks.")
    write(proj, "specific", "Use when the user asks to rebase, squash, or bisect with git.")
    findings = run_all(world_from(proj, home), Config())
    hits = by_check(findings, "generic-description")
    assert [f.contributor_names for f in hits] == [["vague"]]


def test_generic_threshold_tunable(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "two-tokens", "Renders diagrams nicely.")  # renders, diagrams, nicely = 3
    cfg = Config()
    cfg.thresholds.generic_min_distinct_tokens = 5
    findings = run_all(world_from(proj, home), cfg)
    assert by_check(findings, "generic-description")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_checks_heuristics.py -v`
Expected: FAIL — checks not registered, no findings.

- [ ] **Step 3: Implement**

`src/drskill/ledger.py` — extend Thresholds:

```python
class Thresholds(BaseModel):
    near_duplicate: float = 0.85
    description_overlap: float = 0.6
    generic_min_distinct_tokens: int = 2
```

`src/drskill/checks/heuristics.py`:

```python
"""Tier 2 heuristic checks: deterministic, threshold-tuned, always ack-able."""

from __future__ import annotations

from pathlib import Path

from drskill import text
from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Contributor, Finding
from drskill.resolution import World


def _skill_md(world: World) -> list[Contributor]:
    return [
        c
        for c in world.contributors.values()
        if Path(c.id).name == "SKILL.md" and c.frontmatter_valid
    ]


@check("missing-activation")
def missing_activation(world: World, config: Config) -> list[Finding]:
    return [
        make_finding(
            "missing-activation", "warning", [c],
            f"'{c.name}' never says when to use it; the router has to guess",
            fix_commands=[
                f"Start the description in {c.id} with a condition, e.g. 'Use when ...'"
            ],
        )
        for c in _skill_md(world)
        if c.routing_text.strip() and not text.has_activation(c.routing_text)
    ]


@check("generic-description")
def generic_description(world: World, config: Config) -> list[Finding]:
    out = []
    for c in _skill_md(world):
        if not c.routing_text.strip():
            continue
        distinct = {
            t for t in text.content_tokens(c.routing_text)
            if t not in text.GENERIC_VOCAB
        }
        if len(distinct) < config.thresholds.generic_min_distinct_tokens:
            out.append(
                make_finding(
                    "generic-description", "warning", [c],
                    f"'{c.name}' description has no distinguishing words to route on",
                    fix_commands=[
                        f"Name the concrete inputs, outputs, or domain in {c.id}"
                    ],
                )
            )
    return out
```

`src/drskill/checks/__init__.py` — add `heuristics` to run_all's import line:

```python
    from drskill.checks import budget, duplicates, filesystem, heuristics, lockfile, shadowing, spec  # noqa: F401
```

`src/drskill/cli.py` — INIT_TEMPLATE thresholds section becomes:

```python
[thresholds]
near_duplicate = 0.85       # Jaccard similarity that counts as a near duplicate
description_overlap = 0.6   # cosine similarity that clusters descriptions
generic_min_distinct_tokens = 2  # fewer distinctive words than this is too vague
```

- [ ] **Step 4: Add conformance cases**

`tests/conformance/cases/missing-activation/tree/.claude/skills/formatter/SKILL.md`:

```markdown
---
name: formatter
description: Formats source code files.
---
Run the formatter and report what changed.
```

`tests/conformance/cases/missing-activation/expect.toml`:

```toml
[[expect]]
check = "missing-activation"
skills = ["formatter"]

[[forbid]]
check = "generic-description"
skills = ["formatter"]
```

`tests/conformance/cases/generic-description/tree/.claude/skills/vague-helper/SKILL.md`:

```markdown
---
name: vague-helper
description: Helps with various tasks.
---
Does things.
```

`tests/conformance/cases/generic-description/expect.toml`:

```toml
[[expect]]
check = "generic-description"
skills = ["vague-helper"]
```

Also append false-positive guards to `tests/conformance/cases/clean-pair/expect.toml`:

```toml
[[forbid]]
check = "missing-activation"
skills = ["git-helper"]

[[forbid]]
check = "generic-description"
skills = ["git-helper"]

[[forbid]]
check = "description-overlap"
skills = ["git-helper", "docx-report"]

[[forbid]]
check = "opposing-imperatives"
skills = ["git-helper", "docx-report"]
```

- [ ] **Step 5: Run to verify green**

Run: `uv run pytest tests/test_checks_heuristics.py tests/conformance -q` then `uv run pytest -q`
Expected: all pass (existing conformance trees must stay quiet for the new checks; if one now fires a Tier-2 warning, fix the FIXTURE only if its description is genuinely sloppy — e.g. give it a "Use when" phrasing — and note it in the commit message).

- [ ] **Step 6: Commit**

```bash
git add src/drskill/checks/heuristics.py src/drskill/ledger.py src/drskill/checks/__init__.py src/drskill/cli.py tests/test_checks_heuristics.py tests/conformance/cases
git commit -m "feat: missing-activation and generic-description checks"
```

---

### Task 3: description-overlap clusters

**Files:**
- Modify: `src/drskill/checks/heuristics.py`
- Test: `tests/test_checks_heuristics.py` (append)
- Create: conformance case `overlap-pileup/`

**Interfaces:**
- Consumes: `text.shingle_vector/cosine/shared_phrases` (Task 1), `_skill_md` (Task 2), `checks.duplicates.estimate/shingles/signature` for the duplicate carve-out.
- Produces: registered check `description-overlap`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_checks_heuristics.py`:

```python
PILE_A = "Use when the user asks to write project documentation pages."
PILE_B = "Use when the user asks to write project documentation summaries."
PILE_C = "Use when the user asks to write project documentation chapters."


def test_overlap_cluster_fires_with_shared_phrases(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "doc-a", PILE_A, body="a" * 40)
    write(proj, "doc-b", PILE_B, body="b" * 40)
    write(proj, "doc-c", PILE_C, body="c" * 40)
    write(proj, "git-helper", "Use when the user asks to rebase with git.", body="g" * 40)
    findings = run_all(world_from(proj, home), Config())
    hits = by_check(findings, "description-overlap")
    assert len(hits) == 1
    assert set(hits[0].contributor_names) == {"doc-a", "doc-b", "doc-c"}
    assert "write project documentation" in hits[0].message
    assert "git-helper" not in hits[0].contributor_names


def test_overlap_excludes_duplicate_pairs(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    shared_body = "Collect the metrics and summarize each work stream carefully. " * 10
    write(proj, "dup-a", PILE_A, body=shared_body)
    write(proj, "dup-b", PILE_B, body=shared_body + "extra.")
    cfg = Config()
    cfg.thresholds.near_duplicate = 0.5  # make the pair a near-duplicate
    findings = run_all(world_from(proj, home), cfg)
    assert by_check(findings, "near-duplicate")
    assert by_check(findings, "description-overlap") == []


def test_overlap_threshold_tunable(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "doc-a", PILE_A, body="a" * 40)
    write(proj, "doc-b", PILE_B, body="b" * 40)
    cfg = Config()
    cfg.thresholds.description_overlap = 0.999
    findings = run_all(world_from(proj, home), cfg)
    assert by_check(findings, "description-overlap") == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_checks_heuristics.py -q`
Expected: the three new tests fail (check unregistered).

- [ ] **Step 3: Implement** — append to `src/drskill/checks/heuristics.py`:

```python
from itertools import combinations

from drskill.checks.duplicates import estimate, shingles, signature


def _is_duplicate_pair(a: Contributor, b: Contributor, near_threshold: float,
                       sigs: dict[str, list[int]]) -> bool:
    if a.content_hash == b.content_hash:
        return True
    return estimate(sigs[a.id], sigs[b.id]) >= near_threshold


@check("description-overlap")
def description_overlap(world: World, config: Config) -> list[Finding]:
    cs = [c for c in _skill_md(world) if c.routing_text.strip()]
    vecs = {c.id: text.shingle_vector(c.routing_text) for c in cs}
    sigs = {c.id: signature(shingles(f"{c.routing_text}\n{c.body}")) for c in cs}
    parent = {c.id: c.id for c in cs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in combinations(cs, 2):
        if _is_duplicate_pair(a, b, config.thresholds.near_duplicate, sigs):
            continue
        if text.cosine(vecs[a.id], vecs[b.id]) >= config.thresholds.description_overlap:
            parent[find(a.id)] = find(b.id)

    clusters: dict[str, list[Contributor]] = {}
    for c in cs:
        clusters.setdefault(find(c.id), []).append(c)

    out = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda c: c.name)
        phrases = text.shared_phrases([m.routing_text for m in members])[:3]
        claim = f" all claim '{phrases[0]}'" if phrases else " have near-identical descriptions"
        names = ", ".join(m.name for m in members)
        out.append(
            make_finding(
                "description-overlap", "warning", members,
                f"{len(members)} skills ({names}){claim}; "
                "none states an exclusive condition, so routing between them is a coin flip",
                fix_commands=[
                    "Give each description an exclusive 'use when' condition the others lack"
                ],
            )
        )
    return out
```

(Keep all imports at the top of the file when editing for real.)

- [ ] **Step 4: Add the conformance case**

`tests/conformance/cases/overlap-pileup/tree/.claude/skills/doc-pages/SKILL.md`:

```markdown
---
name: doc-pages
description: Use when the user asks to write project documentation pages.
---
Write the documentation pages.
```

`tests/conformance/cases/overlap-pileup/tree/.claude/skills/doc-summaries/SKILL.md`:

```markdown
---
name: doc-summaries
description: Use when the user asks to write project documentation summaries.
---
Write the documentation summaries.
```

`tests/conformance/cases/overlap-pileup/tree/.claude/skills/doc-chapters/SKILL.md`:

```markdown
---
name: doc-chapters
description: Use when the user asks to write project documentation chapters.
---
Write the documentation chapters.
```

`tests/conformance/cases/overlap-pileup/expect.toml`:

```toml
[[expect]]
check = "description-overlap"
skills = ["doc-pages", "doc-summaries", "doc-chapters"]
```

- [ ] **Step 5: Run to verify green**

Run: `uv run pytest tests/test_checks_heuristics.py tests/conformance -q` then `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/drskill/checks/heuristics.py tests/test_checks_heuristics.py tests/conformance/cases/overlap-pileup
git commit -m "feat: description-overlap cluster check with shared trigger phrases"
```

---

### Task 4: opposing-imperatives

**Files:**
- Modify: `src/drskill/checks/heuristics.py`
- Test: `tests/test_checks_heuristics.py` (append)
- Create: conformance case `opposing-imperatives/`

**Interfaces:**
- Consumes: `text.tokenize`, `text.STOPWORDS`, `_skill_md`, `make_finding` with `extra_key`.
- Produces: registered check `opposing-imperatives`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_checks_heuristics.py`:

```python
def test_opposing_imperatives(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "tabs", "Use when formatting code with tabs.",
          body="Always use tabs for indentation.")
    write(proj, "spaces", "Use when formatting code with spaces.",
          body="Never use tabs anywhere in the file.")
    write(proj, "meals", "Use when planning meals for the week.",
          body="Never skip breakfast before coding.")
    findings = run_all(world_from(proj, home), Config())
    hits = by_check(findings, "opposing-imperatives")
    assert len(hits) == 1
    assert set(hits[0].contributor_names) == {"tabs", "spaces"}
    assert "tabs" in hits[0].message


def test_opposing_near_miss_stays_quiet(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "a", "Use when doing a.", body="Always use tabs here.")
    write(proj, "b", "Use when doing b.", body="Never use spaces here.")
    findings = run_all(world_from(proj, home), Config())
    assert by_check(findings, "opposing-imperatives") == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_checks_heuristics.py -q`
Expected: the two new tests fail.

- [ ] **Step 3: Implement** — append to `src/drskill/checks/heuristics.py` (imports to top):

The naive design (exact match on the normalized captured phrase) misses the
canonical case: "always use tabs for indentation" and "never use tabs anywhere"
capture different 4-word windows. Match on the INTERSECTION of content-token
sets between opposite-kind imperatives instead; the near-miss guard is that a
pair with no common content token stays quiet.

```python
import re

_IMPERATIVE = re.compile(r"\b(always|never)\s+((?:\w+[ \t]){0,3}\w+)", re.IGNORECASE)


def _imperative_phrases(c: Contributor) -> dict[str, list[set[str]]]:
    out: dict[str, list[set[str]]] = {"always": [], "never": []}
    for m in _IMPERATIVE.finditer(c.body):
        norm = {t for t in text.tokenize(m.group(2)) if t not in text.STOPWORDS}
        if norm:
            out[m.group(1).lower()].append(norm)
    return out


@check("opposing-imperatives")
def opposing_imperatives(world: World, config: Config) -> list[Finding]:
    cs = _skill_md(world)
    phrases = {c.id: _imperative_phrases(c) for c in cs}
    out = []
    for a, b in combinations(cs, 2):
        seen: set[str] = set()
        for kind_a, kind_b in (("always", "never"), ("never", "always")):
            for sa in phrases[a.id][kind_a]:
                for sb in phrases[b.id][kind_b]:
                    common = sa & sb
                    if not common:
                        continue
                    phrase = " ".join(sorted(common))
                    if phrase in seen:
                        continue
                    seen.add(phrase)
                    out.append(
                        make_finding(
                            "opposing-imperatives", "warning", [a, b],
                            f"'{a.name}' and '{b.name}' give opposite orders about "
                            f"'{phrase}' (always vs never); an agent loading both gets "
                            "contradictory instructions (low-recall check: paraphrased "
                            "contradictions are not detected)",
                            fix_commands=[
                                "Align the two instructions, or scope each to its own condition"
                            ],
                            extra_key=phrase,
                        )
                    )
    return out
```

- [ ] **Step 4: Add the conformance case**

`tests/conformance/cases/opposing-imperatives/tree/.claude/skills/tabs-fan/SKILL.md`:

```markdown
---
name: tabs-fan
description: Use when formatting code that requires tabs.
---
Always use tabs for indentation.
```

`tests/conformance/cases/opposing-imperatives/tree/.claude/skills/tabs-foe/SKILL.md`:

```markdown
---
name: tabs-foe
description: Use when formatting code that forbids tabs.
---
Never use tabs anywhere.
```

`tests/conformance/cases/opposing-imperatives/expect.toml`:

```toml
[[expect]]
check = "opposing-imperatives"
skills = ["tabs-fan", "tabs-foe"]
```

- [ ] **Step 5: Run to verify green, then full suite**

Run: `uv run pytest tests/test_checks_heuristics.py tests/conformance -q` then `uv run pytest -q`
Expected: all pass. (The opposing-imperatives fixture descriptions overlap; if description-overlap fires on the pair that is fine — expect.toml asserts presence, not exclusivity.)

- [ ] **Step 6: Commit**

```bash
git add src/drskill/checks/heuristics.py tests/test_checks_heuristics.py tests/conformance/cases/opposing-imperatives
git commit -m "feat: opposing-imperatives check"
```

---

### Task 5: corpus harness and threshold tuning

**Files:**
- Create: `scripts/corpus.py`
- Modify: `.gitignore` (add `.corpus/`), possibly `src/drskill/ledger.py` + `src/drskill/cli.py` INIT_TEMPLATE + spec (tuned defaults)
- Create: corpus-derived conformance cases (number decided by review)

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: tuned default thresholds; frozen conformance cases.

- [ ] **Step 1: Write `scripts/corpus.py`**

```python
#!/usr/bin/env python3
"""Dev tool: fetch skill corpora and print Tier-2 review sheets.

Not shipped in the wheel. Usage:
    uv run python scripts/corpus.py [--min-cosine 0.4]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from drskill import text  # noqa: E402
from drskill.checks import REGISTRY  # noqa: E402
from drskill.discovery import discover  # noqa: E402
from drskill.harnesses import HarnessDef  # noqa: E402
from drskill.ledger import Config  # noqa: E402
from drskill.resolution import build_world  # noqa: E402

CORPORA = {
    "anthropics-skills": "https://github.com/anthropics/skills",
    "vercel-agent-skills": "https://github.com/vercel-labs/agent-skills",
    "hermes-agent": "https://github.com/NousResearch/hermes-agent",
}
TIER2 = [
    "description-overlap",
    "missing-activation",
    "generic-description",
    "opposing-imperatives",
]


def fetch(root: Path) -> None:
    root.mkdir(exist_ok=True)
    for name, url in CORPORA.items():
        dest = root / name
        if dest.exists():
            continue
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)], check=True
        )


def corpus_world(tree: Path):
    h = HarnessDef(
        id="corpus", display_name="Corpus", verified=True,
        project_paths=["."], recursive=True,
    )
    instances, broken = discover(h, tree, tree / "_nonexistent_home")
    return build_world(instances, {"corpus": h}, broken)


def excerpt(t: str, n: int = 70) -> str:
    t = " ".join(t.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-cosine", type=float, default=0.4)
    args = ap.parse_args()
    root = Path(__file__).resolve().parent.parent / ".corpus"
    fetch(root)
    config = Config()
    from drskill.checks import duplicates, heuristics  # noqa: F401  registers checks
    for name in CORPORA:
        world = corpus_world(root / name)
        skills = [
            c for c in world.contributors.values()
            if c.id.endswith("SKILL.md") and c.frontmatter_valid
        ]
        print(f"\n## {name} — {len(skills)} skills\n")
        print("### overlap pairs (cosine)\n")
        vecs = {c.id: text.shingle_vector(c.routing_text) for c in skills}
        rows = []
        for a, b in combinations(skills, 2):
            score = text.cosine(vecs[a.id], vecs[b.id])
            if score >= args.min_cosine:
                rows.append((score, a, b))
        for score, a, b in sorted(rows, reverse=True, key=lambda r: r[0]):
            print(f"- {score:.2f} `{a.name}` × `{b.name}`")
            print(f"    - {excerpt(a.routing_text)}")
            print(f"    - {excerpt(b.routing_text)}")
        for cid in TIER2[1:]:
            findings = REGISTRY[cid](world, config)
            print(f"\n### {cid} ({len(findings)})\n")
            for f in findings:
                print(f"- {', '.join(f.contributor_names)}: {excerpt(f.message, 100)}")


if __name__ == "__main__":
    main()
```

Add `.corpus/` to `.gitignore`.

- [ ] **Step 2: Run it and review**

Run: `uv run python scripts/corpus.py > /tmp/corpus-sheet.md` then read the sheet. Judge: at cosine 0.6, are the overlap pairs on real corpora genuinely confusable? What fraction of real skills trip missing-activation and generic-description (expect high on some corpora — that matches the ecosystem baseline — but confirm the flagged ones are genuinely vague, not lexicon gaps)? Record observations.

- [ ] **Step 3: Set final defaults**

Adjust `Thresholds` defaults, `INIT_TEMPLATE`, and the spec's ledger section if review says so (raise overlap threshold if 0.6 is noisy on real data; extend `ACTIVATION_PATTERNS` if real well-scoped descriptions are false-flagged — each lexicon addition needs a corpus example in the commit message). Re-run `uv run pytest -q` after any change.

- [ ] **Step 4: Freeze corpus-derived conformance cases**

Pick 2-4 clear verdicts from the sheets (at least one true positive and one false-positive guard). For each, create `tests/conformance/cases/corpus-<short-name>/` with the skill files copied in, a `LICENSE-NOTE.md` naming the upstream repo and license, and an `expect.toml`. Run `uv run pytest tests/conformance -q`.

- [ ] **Step 5: Commit**

```bash
git add scripts/corpus.py .gitignore tests/conformance/cases src/drskill/ledger.py src/drskill/cli.py src/drskill/text.py docs/superpowers/specs/2026-07-20-tier2-heuristics-design.md
git commit -m "feat: corpus tuning harness, tuned Tier-2 defaults, corpus conformance cases"
```

---

### Task 6: README, real-machine triage, final sweep

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README** (plain style, no dashes): add the four new rows to the check table (copy wording from the spec's check sections, one line each); extend the ledger section's threshold example with the two new keys and one sentence each; add one Known limitations line: "The Tier 2 checks are heuristics. They are tuned to stay quiet on real skill sets, and every finding can be acknowledged, but they will miss paraphrased conflicts and flag some judgment calls."

- [ ] **Step 2: Real-machine triage**

Run `uv run drskill scan` from the repo root. Review every Tier-2 finding on the real loadout: genuine ones stay (report them in the summary), false positives mean threshold or lexicon fixes BEFORE release (with a corpus/machine example recorded). Then ack or fix locally as appropriate and confirm a rescan is quiet — the spec's release gate.

- [ ] **Step 3: Full suite + commit**

Run: `uv run pytest -q` — green.

```bash
git add README.md
git commit -m "docs: README for Tier 2 heuristic checks"
```
