"""Approximate token counting. tiktoken loads lazily; if it cannot load
(or cannot fetch its encoding file offline) we fall back to len // 4."""

from __future__ import annotations

_encoder = None
_unavailable = False


def _fallback_count(text: str) -> int:
    return len(text) // 4


def count(text: str) -> int:
    global _encoder, _unavailable
    if not text:
        return 0
    if _unavailable:
        return _fallback_count(text)
    if _encoder is None:
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding("o200k_base")
        except Exception:
            _unavailable = True
            return _fallback_count(text)
    # disallowed_special=() treats special-token text (e.g. a literal
    # "<|endoftext|>" inside a skill body) as ordinary text instead of raising.
    return len(_encoder.encode(text, disallowed_special=()))
