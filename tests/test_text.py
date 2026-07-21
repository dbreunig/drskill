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


def test_shared_phrases_are_real_substrings_not_stripped():
    # Phrases must be verbatim shared text, including stopwords, so they read
    # as real English and can be honestly quoted.
    a = "Delete multiple entities and their associated relations from the knowledge graph"
    b = "Delete multiple relations from the knowledge graph"
    phrases = shared_phrases([a, b])
    assert "relations from the knowledge graph" in phrases  # not "relations knowledge graph"
    assert "delete multiple" in phrases


def test_shared_phrases_longest_first_no_substrings():
    texts = [
        "Use when writing project documentation pages",
        "Use when writing project documentation summaries",
    ]
    phrases = shared_phrases(texts)
    assert phrases[0] == "use when writing project documentation"
    assert "writing project documentation" not in phrases  # substring of a kept phrase


def test_shared_phrases_drops_pure_stopword_phrases():
    # a shared run of only stopwords is noise and must not be quoted
    phrases = shared_phrases([
        "from the archive of old logs",
        "from the vault of new keys",
    ])
    assert all(p not in ("from the", "from", "the") for p in phrases)


def test_shared_phrases_empty_when_nothing_common():
    assert shared_phrases(["rebase git commits", "cook pasta dinner"]) == []


def test_has_activation():
    assert has_activation("Use when the user asks for a Word document.")
    assert has_activation("Invoke for database migrations.")
    assert not has_activation("Formats source code files.")


def test_vocab_contents():
    assert "tasks" in GENERIC_VOCAB and "helps" in GENERIC_VOCAB
    assert "when" in STOPWORDS and "use" in STOPWORDS


def test_tokenize_strips_quote_artifacts():
    assert tokenize("'Berlin', 'Boston'") == ["berlin", "boston"]
    assert tokenize("what's the box") == ["what's", "the", "box"]
