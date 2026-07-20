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
