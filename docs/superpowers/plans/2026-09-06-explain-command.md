# Explain Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `drskill explain "<query>"` — lexical routing simulation per harness with an optional `--deep` model judgment — plus the `[[queries]]` ledger table and the offline `query-routing` check.

**Architecture:** A pure module `src/drskill/explain.py` holds ranking, verdicts, and grouping; `text.py` gains the blended scorer; `ledger.py` gains `routing_margin` and `Config.queries`; a new check reuses the ranking; the CLI command mirrors `audit`'s read-only shape; `deep_llm.py` gains `build_query_judge` reusing `_setup`.

**Tech Stack:** Python 3.11+, typer, pydantic, pytest; dspy only inside deep_llm (lazy).

**Spec:** `docs/superpowers/specs/2026-09-06-explain-reconciled-design.md` (committed with this plan; it reconciles the deferred 2026-07-21 explain spec).

## Global Constraints

- Work only in the worktree; every git/test command `cd`s into it in the same compound command. Never run git in the main checkout.
- Full suite before each commit: `uv run --extra connect --extra deep pytest` (bare command misses extras).
- `explain` is read-only: no seen-state, ledger, cache, or baseline writes; the check tier never makes model calls.
- Command-kind contributors never appear in rankings.
- All skill-controlled text in output passes through the report sanitizer.
- Transcribe the plan's docstrings and comments verbatim. Commit per task, conventional prefix, ending with:
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

## Code facts

- `text.py` has `content_tokens(text) -> list[str]`, `shingle_vector(text, k=2) -> dict[str,int]`, `cosine(a, b) -> float` (text.py:59-91).
- `ledger.Thresholds` (ledger.py:25-28) has three fields; `Config` (ledger.py:43-47) has budget/thresholds/deep/ack; `load_effective_config` merges only acks across scopes, so new fields are automatically non-merging.
- `checks/__init__.py` has `@check(id)`, `fingerprint(check_id, contributors, extra, texts)`, `make_finding(...)`.
- `report.py:21` has private `_sanitize(text)`.
- `World.effective(harness_id)` (resolution.py:150-156) yields unshadowed contributors in load order; `world.harnesses` maps id -> HarnessDef.
- `deep_llm._setup(model_id)` returns a configured `dspy.LM`; `build_judge`/`build_rewriter` are the pattern to copy; errors raise `DeepUnavailableError`. CLI deep wiring pattern: cli.py scan ~lines 216-260 (`deep.load_user_env(home)`, try/except `DeepUnavailableError` exit 1, red text).
- Tests: `tests/test_deep_cli.py` stubs `deep_llm.build_*` with monkeypatch; `tests/test_report.py` has `tables_to_text`; check tests use `run_check(check_id, world, config)` helpers per file.
- The `audit` command (cli.py:727+) is the read-only command shape to mirror; `list` shows console setup.

---

### Task 1: The blended routing scorer

**Files:**
- Modify: `src/drskill/text.py`
- Test: `tests/test_text.py`

**Interfaces:**
- Produces: `routing_score(query: str, description: str) -> float` in [0, 1], plus module constants `ROUTING_UNIGRAM_WEIGHT = 0.7`, `ROUTING_SHINGLE_WEIGHT = 0.3`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_text.py` (mirror its import style):

```python
def test_routing_score_blends_unigram_and_shingle():
    q = "summarize a pdf document"
    exact = text.routing_score(q, "summarize a pdf document")
    related = text.routing_score(q, "summarize pdf files and documents")
    unrelated = text.routing_score(q, "deploy kubernetes clusters")
    assert exact == 1.0
    assert 0 < related < exact
    assert unrelated == 0.0


def test_routing_score_handles_empty_inputs():
    assert text.routing_score("", "anything") == 0.0
    assert text.routing_score("query", "") == 0.0


def test_routing_score_orders_by_relevance():
    q = "find hotels near the airport"
    close = text.routing_score(q, "find hotels and lodging near airports")
    far = text.routing_score(q, "find restaurants in the city center")
    assert close > far
```

(If the file imports functions directly rather than the module, adapt the call sites, assertions identical.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_text.py -v -k routing` → AttributeError.

- [ ] **Step 3: Implement**

Append to `src/drskill/text.py`:

```python
ROUTING_UNIGRAM_WEIGHT = 0.7
ROUTING_SHINGLE_WEIGHT = 0.3


def routing_score(query: str, description: str) -> float:
    """Blend of unigram and 2-word-shingle cosine over content tokens.
    A cheap stand-in for a harness router: word overlap carries most of
    the weight, phrase overlap breaks ties between similar descriptions."""
    q_tokens = content_tokens(query)
    d_tokens = content_tokens(description)
    if not q_tokens or not d_tokens:
        return 0.0
    uni = cosine(Counter(q_tokens), Counter(d_tokens))
    shi = cosine(shingle_vector(query), shingle_vector(description))
    return ROUTING_UNIGRAM_WEIGHT * uni + ROUTING_SHINGLE_WEIGHT * shi
```

Check `text.py`'s imports: it may not import `Counter`; add `from collections import Counter` if missing. If `cosine` expects plain dicts, `Counter` works (it is a dict). If `shingle_vector` on a single-word string returns an empty dict, `cosine` must tolerate it — read `cosine`; if it divides by zero on empty vectors, guard: `shi = cosine(...) if len(q_tokens) > 1 and len(d_tokens) > 1 else 0.0` and renormalize is NOT needed (exact-match test uses a 4-word query so shingles exist; the empty-shingle case only loses the 0.3 component). If the exact-match assertion `== 1.0` fails due to float rounding, change it to `> 0.999` and note it in your report.

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_text.py -v` → PASS.
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/text.py tests/test_text.py
git commit -m "feat: add the blended routing scorer"
```

---

### Task 2: The explain module

**Files:**
- Create: `src/drskill/explain.py`
- Test: `tests/test_explain.py`

**Interfaces:**
- Produces:

```python
SCORE_FLOOR = 0.1
TOP_N = 5

@dataclass
class Row:
    score: float
    contributor: Contributor

@dataclass
class HarnessRanking:
    harness: str
    rows: list[Row]          # descending score, zero scores dropped, max TOP_N
    verdict: str             # "none" | "contested" | "routes"
    top_name: str | None     # winner's name when verdict == "routes" or "contested"

def rank(world, query: str, margin: float, harness: str | None = None) -> list[HarnessRanking]
def group_rankings(rankings: list[HarnessRanking]) -> list[tuple[list[str], HarnessRanking]]
```

`rank` iterates the world's harnesses (or the one named), scores each harness's `world.effective(hid)` contributors with `text.routing_score(query, c.routing_text)`, skipping `c.kind == "command"`. Rows with score 0 drop; the rest sort descending by (score, name) with name ascending as tiebreak; keep `TOP_N`. Verdict: "none" if no rows or top score < `SCORE_FLOOR`; "contested" if a second row exists and `rows[0].score - rows[1].score < margin`; else "routes". `top_name` is `rows[0].contributor.name` when rows exist and verdict != "none", else None. `group_rankings` merges harnesses whose (verdict, [(name, round(score, 6)) for rows]) are identical, preserving first-seen order, returning (harness id list, representative ranking).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_explain.py`:

```python
from drskill import explain
from drskill.harnesses import HarnessDef
from drskill.models import Deployment
from drskill.resolution import World
from tests.test_models import make_contributor


def _world(*specs, harnesses=("h1",)):
    # specs: (name, routing_text) or (name, routing_text, kind)
    contributors = {}
    for i, spec in enumerate(specs):
        name, routing_text, *rest = spec
        kind = rest[0] if rest else "skill"
        c = make_contributor(id=f"/x/{name}", name=name, kind=kind,
                             routing_text=routing_text)
        for h in harnesses:
            c.deployments.append(Deployment(harness=h, path=f"/x/{name}",
                                            scope="project", via_symlink=False,
                                            order=i))
        contributors[c.id] = c
    return World(contributors=contributors,
                 harnesses={h: HarnessDef(id=h, display_name=h) for h in harnesses})


def test_rank_orders_and_floors():
    w = _world(("pdf", "summarize pdf documents"),
               ("deploy", "deploy kubernetes clusters"))
    [r] = explain.rank(w, "summarize a pdf", margin=0.1)
    assert r.verdict == "routes"
    assert r.top_name == "pdf"
    assert [row.contributor.name for row in r.rows] == ["pdf"]


def test_rank_contested_when_gap_is_small():
    w = _world(("pdf-a", "summarize pdf documents"),
               ("pdf-b", "summarize pdf documents fast"))
    [r] = explain.rank(w, "summarize pdf documents", margin=0.1)
    assert r.verdict == "contested"


def test_rank_none_when_nothing_matches():
    w = _world(("deploy", "deploy kubernetes clusters"))
    [r] = explain.rank(w, "translate portuguese poetry", margin=0.1)
    assert r.verdict == "none"
    assert r.rows == []


def test_rank_skips_commands():
    w = _world(("pdf", "summarize pdf documents"),
               ("pdf-cmd", "summarize pdf documents", "command"))
    [r] = explain.rank(w, "summarize pdf", margin=0.1)
    assert [row.contributor.name for row in r.rows] == ["pdf"]


def test_rank_single_harness_filter():
    w = _world(("pdf", "summarize pdf documents"), harnesses=("h1", "h2"))
    rankings = explain.rank(w, "summarize pdf", margin=0.1, harness="h2")
    assert [r.harness for r in rankings] == ["h2"]


def test_group_rankings_collapses_identical():
    w = _world(("pdf", "summarize pdf documents"), harnesses=("h1", "h2"))
    grouped = explain.group_rankings(explain.rank(w, "summarize pdf", margin=0.1))
    assert len(grouped) == 1
    ids, rep = grouped[0]
    assert ids == ["h1", "h2"]
    assert rep.top_name == "pdf"
```

Note: `make_contributor` (tests/test_models.py:14) accepts overrides; confirm it passes `routing_text` and `kind` through to `Contributor` (it uses `defaults.update(overrides)`, so it does).

- [ ] **Step 2: Verify failure** — `uv run pytest tests/test_explain.py -v` → ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `src/drskill/explain.py`:

```python
"""Simulate routing for a query: score every effective description per
harness, rank, and verdict. This is drskill's own similarity model, not
any harness's real router — every rendered output must say so."""

from __future__ import annotations

from dataclasses import dataclass

from drskill import text
from drskill.models import Contributor

SCORE_FLOOR = 0.1
TOP_N = 5


@dataclass
class Row:
    score: float
    contributor: Contributor


@dataclass
class HarnessRanking:
    harness: str
    rows: list[Row]
    verdict: str  # none | contested | routes
    top_name: str | None


def rank(world, query: str, margin: float, harness: str | None = None) -> list[HarnessRanking]:
    out: list[HarnessRanking] = []
    for hid in sorted(world.harnesses):
        if harness is not None and hid != harness:
            continue
        rows = []
        for c in world.effective(hid):
            if c.kind == "command":
                continue  # commands are invoked explicitly, never routed
            score = text.routing_score(query, c.routing_text)
            if score > 0:
                rows.append(Row(score=score, contributor=c))
        rows.sort(key=lambda r: (-r.score, r.contributor.name))
        rows = rows[:TOP_N]
        if not rows or rows[0].score < SCORE_FLOOR:
            out.append(HarnessRanking(hid, [] if not rows or rows[0].score < SCORE_FLOOR else rows,
                                      "none", None))
            continue
        contested = len(rows) > 1 and rows[0].score - rows[1].score < margin
        out.append(HarnessRanking(hid, rows,
                                  "contested" if contested else "routes",
                                  rows[0].contributor.name))
    return out


def group_rankings(rankings: list[HarnessRanking]) -> list[tuple[list[str], HarnessRanking]]:
    """Harnesses whose whole ranked result is identical share one table."""
    grouped: list[tuple[list[str], HarnessRanking]] = []
    keys: dict[tuple, int] = {}
    for r in rankings:
        key = (r.verdict, tuple((row.contributor.name, round(row.score, 6)) for row in r.rows))
        if key in keys:
            grouped[keys[key]][0].append(r.harness)
        else:
            keys[key] = len(grouped)
            grouped.append(([r.harness], r))
    return grouped
```

One wrinkle in `rank`'s "none" branch as written: when rows exist but the top is under the floor, the spec says verdict "none" and the rows still should not print (zero-score rows never print; sub-floor rows produce "none"). Simplify the branch to always empty the rows on "none":

```python
        if not rows or rows[0].score < SCORE_FLOOR:
            out.append(HarnessRanking(hid, [], "none", None))
            continue
```

Use this simplified form; the test `test_rank_none_when_nothing_matches` pins it.

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_explain.py -v` → PASS.
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/explain.py tests/test_explain.py
git commit -m "feat: add the explain ranking module"
```

---

### Task 3: Ledger fields

**Files:**
- Modify: `src/drskill/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Produces: `Thresholds.routing_margin: float = 0.1`; `class Query(BaseModel): query: str; expect: str | None = None`; `Config.queries: list[Query] = Field(default_factory=list)`. `load_effective_config` untouched (merges only acks), so queries and the margin stay with the mode's own ledger.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger.py` (mirror its fixture style for writing a toml and loading config — find how existing tests write `drskill.toml` content and call `load_config`/`load_effective_config`):

```python
def test_queries_table_parses(tmp_path):
    p = tmp_path / "drskill.toml"
    p.write_text('[[queries]]\nquery = "summarize this pdf"\nexpect = "pdf-tools"\n'
                 '[[queries]]\nquery = "deploy the app"\n')
    cfg = load_config(p)
    assert [q.query for q in cfg.queries] == ["summarize this pdf", "deploy the app"]
    assert cfg.queries[0].expect == "pdf-tools"
    assert cfg.queries[1].expect is None


def test_routing_margin_default_and_override(tmp_path):
    p = tmp_path / "drskill.toml"
    assert load_config(p).thresholds.routing_margin == 0.1
    p.write_text("[thresholds]\nrouting_margin = 0.25\n")
    assert load_config(p).thresholds.routing_margin == 0.25


def test_queries_do_not_merge_across_scopes(tmp_path):
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    home.mkdir(); proj.mkdir()
    (home / ".drskill.toml").write_text('[[queries]]\nquery = "global only"\n')
    cfg = load_effective_config(proj, home, global_mode=False)
    assert cfg.queries == []
```

(Adapt import names to the file's existing imports.)

- [ ] **Step 2: Verify failure** — `uv run pytest tests/test_ledger.py -v -k "queries or routing_margin"`.

- [ ] **Step 3: Implement**

In `src/drskill/ledger.py`: add `routing_margin: float = 0.1` to `Thresholds`; add after `Ack`:

```python
class Query(BaseModel):
    """A routing expectation: this query should route cleanly, optionally
    to a specific skill. Non-merging, like budgets and thresholds."""
    query: str
    expect: str | None = None
```

and `queries: list[Query] = Field(default_factory=list)` on `Config`.

- [ ] **Step 4: Verify pass**, **Step 5: Full suite, commit**

```bash
git add src/drskill/ledger.py tests/test_ledger.py
git commit -m "feat: add routing_margin and the queries ledger table"
```

---

### Task 4: The query-routing check

**Files:**
- Create: `src/drskill/checks/query_routing.py`
- Modify: whatever registers check modules (find how `checks/run_all` imports its modules — `checks/__init__.py` or a module list; mirror how `skill_shell` is registered/imported)
- Test: `tests/test_checks_query_routing.py`

**Interfaces:**
- Produces: `@check("query-routing")` emitting one warning per (query, harness-group) that is contested or misses its `expect`. Message shape: `Query "<q>" is contested between <a> and <b>` or `Query "<q>" routes to <winner>, expected <expect>` — with the harness list carried in the finding's `harnesses` field. Fingerprint via `fingerprint("query-routing", contributors, extra=q, texts=[q, *[f"{row.contributor.name}: {row.contributor.routing_text}" for row in rows]])` so acks survive unrelated edits. No model calls ever.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_checks_query_routing.py`, reusing the world-building helper style from `tests/test_explain.py` (import `_world`? it is private to that module — copy the small helper instead) and the `run_check`/REGISTRY invocation style from other check tests (read `tests/test_checks_skill_shell.py`'s `run_check` helper and mirror it):

```python
def test_contested_query_warns():
    w = world_with(("pdf-a", "summarize pdf documents"),
                   ("pdf-b", "summarize pdf documents fast"))
    cfg = Config(queries=[Query(query="summarize pdf documents")])
    findings = run_check("query-routing", w, cfg)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "contested" in findings[0].message


def test_expect_miss_warns():
    w = world_with(("pdf", "summarize pdf documents"),
                   ("notes", "take meeting notes"))
    cfg = Config(queries=[Query(query="summarize pdf documents", expect="notes")])
    findings = run_check("query-routing", w, cfg)
    assert len(findings) == 1
    assert "expected notes" in findings[0].message


def test_clean_query_is_silent():
    w = world_with(("pdf", "summarize pdf documents"),
                   ("deploy", "deploy kubernetes clusters"))
    cfg = Config(queries=[Query(query="summarize pdf documents", expect="pdf")])
    assert run_check("query-routing", w, cfg) == []


def test_no_queries_is_silent():
    w = world_with(("pdf", "summarize pdf documents"))
    assert run_check("query-routing", w, Config()) == []
```

`world_with` is the copied helper building a World with one harness ("h1") as in test_explain.py.

- [ ] **Step 2: Verify failure** — unknown check id / import error.

- [ ] **Step 3: Implement**

Create `src/drskill/checks/query_routing.py`:

```python
"""Configured routing expectations, checked lexically on every scan.
Never calls a model: scans must stay offline; drskill explain --deep is
where the model-judged version of the same question lives."""

from __future__ import annotations

from drskill import explain
from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World


@check("query-routing")
def query_routing(world: World, config: Config) -> list[Finding]:
    out: list[Finding] = []
    for q in config.queries:
        rankings = explain.rank(world, q.query, margin=config.thresholds.routing_margin)
        for harness_ids, r in explain.group_rankings(rankings):
            problem = None
            if r.verdict == "contested":
                a, b = r.rows[0].contributor.name, r.rows[1].contributor.name
                problem = f'Query "{q.query}" is contested between {a} and {b}'
            elif q.expect and r.verdict == "routes" and r.top_name != q.expect:
                problem = f'Query "{q.query}" routes to {r.top_name}, expected {q.expect}'
            elif q.expect and r.verdict == "none":
                problem = f'Query "{q.query}" matches nothing, expected {q.expect}'
            if problem is None:
                continue
            contributors = [row.contributor for row in r.rows]
            out.append(make_finding(
                "query-routing", "warning", contributors, problem,
                harnesses=harness_ids, extra_key=q.query,
                fingerprint_texts=[q.query, *[
                    f"{row.contributor.name}: {row.contributor.routing_text}"
                    for row in r.rows]],
            ))
    return out
```

Read `make_finding`'s actual signature first (checks/__init__.py:39+): the keyword for fingerprint texts may be named differently (`texts`, `fingerprint_texts`, or the fingerprint may be computed internally from an argument). Match the real API exactly; the intent is: fingerprint over the query plus each ranked name+description. If `make_finding` computes the fingerprint itself from `texts`, pass that. Register the module the same way other check modules are made importable (grep for how `run_all` discovers checks — if `checks/__init__.py` imports each module or `run_all` imports a list, add `query_routing` there).

Edge: `contributors` may be empty when verdict is "none" (rows emptied). Check whether `make_finding`/`fingerprint` tolerate an empty contributor list; if not, fall back to passing the full effective set is wrong — instead pass `[]` if allowed, else skip contributors-based parts by passing the fingerprint texts only. Resolve against the real helper and note the choice in your report.

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_checks_query_routing.py -v`.
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/checks/query_routing.py tests/test_checks_query_routing.py <registration file>
git commit -m "feat: check configured queries keep routing predictably"
```

---

### Task 5: The explain CLI command

**Files:**
- Modify: `src/drskill/cli.py`, `src/drskill/report.py` (public sanitizer)
- Test: `tests/test_cli_explain.py`

**Interfaces:**
- Produces: `drskill explain "<query>" [--global] [--harness <id>] [--json]` (deep flag comes in Task 6). `report.sanitize(text)` public alias for `_sanitize`.

Behavior: run the scan pipeline read-only (mirror how `list` builds its world — find the `list` command's `run_scan`/`_scan_with_status` usage and copy it, including `--global` handling if `list` has it; if `list` lacks `--global`, mirror `scan`'s `global_only` plumbing). Load config via `load_effective_config` for the margin. Rank, group, and print per group:

```
<harness display names or "all N harnesses">
  verdict line: "routes to <top>" | "contested between <a> and <b>" | "no skill matches"
  rank. score  name  description (one_line, sanitized)
```

End with the disclaimer: `Scores are drskill's own similarity model, not the harness router.` `--json` prints a JSON document `{"query": ..., "floor": ..., "margin": ..., "harnesses": [{"harness": ..., "verdict": ..., "top": ..., "rows": [{"score": ..., "name": ..., "description": ...}]}]}` from the pre-grouping rankings. Unknown `--harness` id errors like scan does (find and mirror that message). The command never writes: no `state.mark_seen`, no ledger writes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_explain.py` patterned on `tests/test_deep_cli.py`'s world setup (it writes real skill files under a tmp project and runs the CLI via CliRunner with DRSKILL_HOME monkeypatched — read its fixture and mirror; if it stubs run_scan instead, mirror that). Tests:

```python
def test_explain_prints_ranking_and_disclaimer(...):
    # project with two skills: "pdf" (summarize pdf documents), "deploy" (deploy kubernetes clusters)
    result = runner.invoke(app, ["explain", "summarize a pdf document"])
    assert result.exit_code == 0, result.output
    assert "routes to pdf" in result.output
    assert "pdf" in result.output
    assert "drskill's own similarity model" in result.output


def test_explain_json_shape(...):
    result = runner.invoke(app, ["explain", "summarize a pdf document", "--json"])
    data = json.loads(result.output)
    assert data["query"] == "summarize a pdf document"
    assert data["harnesses"][0]["rows"][0]["name"] == "pdf"


def test_explain_unknown_harness_errors(...):
    result = runner.invoke(app, ["explain", "x", "--harness", "not-real"])
    assert result.exit_code == 1


def test_explain_is_read_only(...):
    # after running explain, no .drskill state/cache files were created in the project
    runner.invoke(app, ["explain", "summarize a pdf document"])
    assert not (project / ".drskill").exists()
```

Flesh the fixture into real code while writing the test file — no placeholder bodies; every `...` above becomes the actual fixture arguments once you've read the sibling file.

- [ ] **Step 2: Verify failure** — `No such command 'explain'`.

- [ ] **Step 3: Implement**

`report.py`: below `_sanitize`, add:

```python
sanitize = _sanitize  # public: other commands render skill-controlled text too
```

`cli.py`: add a top-level command after `audit` (transcribe, adapting the world-build lines to the real helpers you found):

```python
@app.command()
def explain(
    query: str = typer.Argument(..., help="a user request, quoted"),
    global_mode: bool = typer.Option(False, "--global", help="scan the machine-wide setup instead of a project"),
    harness: str | None = typer.Option(None, "--harness", help="limit to one harness"),
    json_out: bool = typer.Option(False, "--json", help="machine-readable output"),
) -> None:
    """Simulate where a request would route across your harnesses."""
    from drskill import explain as explain_mod
    from drskill.report import sanitize
    from drskill.text import one_line

    root, home = Path.cwd(), _home()
    config = load_effective_config(root, home, global_mode)
    world, _ = _scan_with_status(lambda p: run_scan(root, home, global_only=global_mode, progress=p))
    if harness is not None and harness not in world.harnesses:
        typer.echo(f"Unknown or undetected harness {harness!r}.")
        raise typer.Exit(1)
    rankings = explain_mod.rank(world, query, margin=config.thresholds.routing_margin,
                                harness=harness)
    if json_out:
        doc = {
            "query": query,
            "floor": explain_mod.SCORE_FLOOR,
            "margin": config.thresholds.routing_margin,
            "harnesses": [
                {"harness": r.harness, "verdict": r.verdict, "top": r.top_name,
                 "rows": [{"score": round(row.score, 4),
                           "name": row.contributor.name,
                           "description": row.contributor.routing_text}
                          for row in r.rows]}
                for r in rankings
            ],
        }
        typer.echo(json.dumps(doc, indent=2))
        return
    for harness_ids, r in explain_mod.group_rankings(rankings):
        names = [world.harnesses[h].display_name for h in harness_ids]
        label = names[0] if len(names) == 1 else f"all {len(names)} harnesses ({', '.join(names)})"
        typer.echo(f"\n{label}")
        if r.verdict == "none":
            typer.echo("  no skill matches")
        elif r.verdict == "contested":
            a, b = r.rows[0].contributor.name, r.rows[1].contributor.name
            typer.echo(f"  contested between {sanitize(a)} and {sanitize(b)}")
        else:
            typer.echo(f"  routes to {sanitize(r.top_name)}")
        for i, row in enumerate(r.rows, start=1):
            desc = one_line(row.contributor.routing_text, 70)
            typer.echo(f"  {i}. {row.score:.2f}  {sanitize(row.contributor.name)}  {sanitize(desc)}")
    typer.echo("\nScores are drskill's own similarity model, not the harness router.")
```

Adapt the two world-build lines to the actual helpers (`_scan_with_status` exists — the loadout commands use it; check whether `run_scan` takes `global_only`). Verify `json` and `Path` are already imported in cli.py (they are).

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_cli_explain.py -v`.
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/cli.py src/drskill/report.py tests/test_cli_explain.py
git commit -m "feat: add drskill explain"
```

---

### Task 6: The --deep query judge

**Files:**
- Modify: `src/drskill/deep_llm.py`, `src/drskill/explain.py`, `src/drskill/cli.py`
- Test: `tests/test_cli_explain.py`

**Interfaces:**
- Produces: `deep_llm.build_query_judge(model_id) -> Callable[[str, list[tuple[str, str]]], QueryJudgeResult | None]` where the second argument is `[(name, description), ...]`; `explain.QueryJudgeResult` dataclass with `routed: str | None`, `contested: bool`, `rationale: str`; `drskill explain --deep` runs one judge call per ranking group and prints the judged verdict above the lexical one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_explain.py`, stubbing like `test_deep_cli.py` does:

```python
def test_explain_deep_prints_model_verdict(..., monkeypatch):
    from drskill import deep_llm, explain as explain_mod

    def fake_builder(model_id):
        def judge(query, candidates):
            return explain_mod.QueryJudgeResult(
                routed="pdf", contested=False,
                rationale="the pdf skill names the format")
        return judge

    monkeypatch.setattr(deep_llm, "build_query_judge", fake_builder)
    result = runner.invoke(app, ["explain", "summarize a pdf document", "--deep"])
    assert result.exit_code == 0, result.output
    assert "model verdict: routes to pdf" in result.output
    assert "the pdf skill names the format" in result.output
    assert "drskill's own similarity model" not in result.output
    assert "model's judgment" in result.output


def test_explain_deep_unavailable_exits_one(..., monkeypatch):
    from drskill import deep_llm

    def boom(model_id):
        raise deep_llm.DeepUnavailableError("no key configured")

    monkeypatch.setattr(deep_llm, "build_query_judge", boom)
    result = runner.invoke(app, ["explain", "x", "--deep"])
    assert result.exit_code == 1
    assert "no key configured" in result.output
```

- [ ] **Step 2: Verify failure** — AttributeError on `build_query_judge`.

- [ ] **Step 3: Implement**

`explain.py` gains:

```python
@dataclass
class QueryJudgeResult:
    routed: str | None
    contested: bool
    rationale: str
```

`deep_llm.py` gains, mirroring `build_judge`'s structure (lazy dspy import through `_setup`, `last_error` attribute, None on per-call failure):

```python
def build_query_judge(model_id: str):
    """One call per ranking: which candidate would a router pick for this
    query, if any, and is the choice contested."""
    import dspy

    from drskill.explain import QueryJudgeResult

    lm = _setup(model_id)

    class QueryJudge(dspy.Signature):
        """Decide which candidate skill a request router would invoke for
        the user query, if any. Contested means two candidates are close
        enough that routing is unpredictable."""

        query: str = dspy.InputField()
        candidates: str = dspy.InputField(desc="numbered 'name: description' lines")
        routed: str = dspy.OutputField(desc="the winning candidate's name, or 'none'")
        contested: bool = dspy.OutputField()
        rationale: str = dspy.OutputField(desc="one sentence")

    predict = dspy.Predict(QueryJudge)

    def judge(query: str, candidates: list[tuple[str, str]]):
        lines = "\n".join(f"{i}. {n}: {d}" for i, (n, d) in enumerate(candidates, 1))
        try:
            with dspy.context(lm=lm):
                out = predict(query=query, candidates=lines)
        except Exception as e:
            judge.last_error = str(e)
            return None
        routed = (out.routed or "").strip()
        return QueryJudgeResult(
            routed=None if routed.lower() in ("", "none") else routed,
            contested=bool(out.contested),
            rationale=str(out.rationale or "").strip(),
        )

    judge.last_error = None
    return judge
```

Before transcribing, read `build_judge` in deep_llm.py and match ITS exact idioms (how it wraps the LM context, how `last_error` is attached, exception scope). If `build_judge` uses a different context/call pattern than `dspy.context(lm=lm)`, copy that pattern instead — consistency with the file wins over this sketch.

`cli.py` explain command gains `deep: bool = typer.Option(False, "--deep", help="ask the configured model to judge the routing")`. When set: `deep.load_user_env(home)` (match scan's deep setup lines), build via `deep_llm.build_query_judge(config.deep.model)` inside try/except `DeepUnavailableError` → red message, exit 1 (mirror scan's handler). In the non-json render loop, for each group call `judge(query, [(row.contributor.name, row.contributor.routing_text) for row in r.rows])` when rows exist; if a result comes back, print before the lexical verdict line:

```python
            v = judge(query, [(row.contributor.name, row.contributor.routing_text) for row in r.rows]) if r.rows else None
            if v is not None:
                target = sanitize(v.routed) if v.routed else "nothing"
                flavor = "contested; " if v.contested else ""
                typer.echo(f"  model verdict: routes to {target} ({flavor}{sanitize(v.rationale)})")
```

and swap the trailing disclaimer when `--deep` ran: `Verdicts above are the configured model's judgment of drskill's candidate list, not the harness router.` Include judge results in `--json` when deep (add a `"model"` key per harness entry with routed/contested/rationale or null). After the loop, print `judge.last_error` if set (mirror scan's pattern).

- [ ] **Step 4: Verify pass** — `uv run pytest tests/test_cli_explain.py -v` and `uv run pytest tests/test_deep_cli.py -v` (ensure no cross-contamination).
- [ ] **Step 5: Full suite, commit**

```bash
git add src/drskill/deep_llm.py src/drskill/explain.py src/drskill/cli.py tests/test_cli_explain.py
git commit -m "feat: judge explain rankings with the configured model"
```

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the docs**

Add an `## Explain routing` section after the scan/report sections (read the README's structure and pick the natural spot near where checks are described): what `drskill explain "<query>"` shows, the three verdicts, `--deep`, the `[[queries]]` table with a toml example, and that `query-routing` warnings gate `--ci`. Keep the README's plain voice. Also add `query-routing` to the checks list section if the README enumerates checks (grep "## Checks").

- [ ] **Step 2: Full suite, commit**

```bash
git add README.md
git commit -m "docs: describe drskill explain and the query-routing check"
```
