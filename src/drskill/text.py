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
    might must do does did done being been am were not no yes here
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
            if not any(phrase in longer for longer in kept):
                kept.append(phrase)
    return kept


def has_activation(text: str) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in ACTIVATION_PATTERNS)
