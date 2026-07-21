"""Shared text heuristics: tokenizing, similarity, and pattern data.

Everything here is deterministic and dependency free. The Tier 2 checks and
the corpus tuning script build on these primitives.
"""

from __future__ import annotations

import re

# Tokens must end alphanumeric so quoted text ('Berlin',) does not leave a
# trailing apostrophe on the token; internal apostrophes (what's) survive.
_WORD = re.compile(r"[a-z0-9](?:[a-z0-9'-]*[a-z0-9])?")

STOPWORDS: frozenset[str] = frozenset(
    """a an and are as at be by for from has have if in into is it its of on or
    so such that the their then there these this to was will with you your i we
    when where how what which who whom whose why can could should would may
    might must do does did done being been am were not no yes here
    use uses used using user users skill skills ask asks asked asking""".split()
)

# Substring patterns matched against the raw lowercased description.
# Corpus tuning 2026-07-20: single words that signal a condition (when,
# whenever, trigger*, invoke*) are matched as whole tokens instead, so a
# mid-sentence "when building new UI" counts (found in anthropics/skills
# frontend-design, previously a false positive).
ACTIVATION_PATTERNS: tuple[str, ...] = (
    "for questions about",
    "if the user",
    "if you",
    "before ",
    "after ",
    "during ",
)

_ACTIVATION_TOKENS: frozenset[str] = frozenset(
    {
        "when", "whenever",
        "trigger", "triggers", "triggered", "triggering",
        "invoke", "invokes", "invoked", "invoking",
    }
)

GENERIC_VOCAB: frozenset[str] = frozenset(
    """help helps assist assists task tasks various general support supports
    work works handle handles manage manages tool tools thing things stuff
    item items way ways""".split()
)


def one_line(s: str, limit: int = 100) -> str:
    """Collapse text to a single line and truncate it. MCP tool
    descriptions can run to paragraphs; a report line needs one clause."""
    flat = " ".join(s.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


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
    if not den:
        return 0.0
    return min(1.0, num / den)


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
            # Word-boundary containment: "document" is not part of the kept
            # phrase "write project documentation" even though it is a raw
            # substring of it.
            if not any(f" {phrase} " in f" {longer} " for longer in kept):
                kept.append(phrase)
    return kept


def has_activation(text: str) -> bool:
    lowered = text.lower()
    if any(p in lowered for p in ACTIVATION_PATTERNS):
        return True
    return not _ACTIVATION_TOKENS.isdisjoint(tokenize(lowered))
