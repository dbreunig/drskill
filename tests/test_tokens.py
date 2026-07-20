import sys

from drskill import tokens


def test_count_returns_positive_int():
    n = tokens.count("Use this skill when writing documentation.")
    assert isinstance(n, int) and n > 0


def test_empty_string_is_zero():
    assert tokens.count("") == 0


def test_fallback_is_chars_over_four():
    assert tokens._fallback_count("x" * 40) == 10


def test_cli_module_does_not_import_tiktoken_eagerly():
    # importing drskill.tokens must not pull in tiktoken
    for mod in ["tiktoken", "drskill.tokens"]:
        sys.modules.pop(mod, None)
    import drskill.tokens  # noqa: F401

    assert "tiktoken" not in sys.modules


def test_count_survives_special_token_text():
    n = tokens.count("prefix <|endoftext|> suffix")
    assert isinstance(n, int) and n > 0
