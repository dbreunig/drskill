import pytest

from drskill import content, loadout_drift
from drskill.models import Contributor, Provenance, TokenCost

FILES = [{"path": "SKILL.md", "data": b"body\n", "executable": False}]
DIR_HASH = content.manifest_hash(FILES)
SKILL_HASH = "sha256:" + "ab" * 32


def contributor(name, kind="skill", content_hash=SKILL_HASH, id=None):
    return Contributor(
        id=id or f"/tmp/{name}", kind=kind, name=name,
        source=Provenance(kind="unmanaged", source=None), scope="project",
        token_cost=TokenCost(catalog_tokens=1, body_tokens=1),
        content_hash=content_hash,
    )


def entry(name="citation", source_type="drskill", content_hash=DIR_HASH,
          kind="skill", metadata=None):
    return {"kind": kind, "selector": f"{kind}:{name}", "name": name,
            "source_type": source_type, "source_reference": source_type,
            "content_hash": content_hash, "local_only": source_type == "local",
            "metadata": metadata if metadata is not None else {}}


@pytest.fixture
def collected(monkeypatch):
    monkeypatch.setattr(content, "collect_files", lambda c: list(FILES))


def classify_one(e, contributors):
    return loadout_drift.classify_entries([e], contributors)[0]


def test_hosted_entry_matches_and_changes(collected):
    assert classify_one(entry(), [contributor("citation")]).state == "matches"
    changed = entry(content_hash="sha256:" + "00" * 32)
    assert classify_one(changed, [contributor("citation")]).state == "changed"


def test_github_entry_uses_the_directory_hash(collected):
    e = entry(source_type="github", content_hash=SKILL_HASH,
              metadata={"directory_hash": DIR_HASH})
    assert classify_one(e, [contributor("citation")]).state == "matches"
    e["metadata"]["directory_hash"] = "sha256:" + "00" * 32
    assert classify_one(e, [contributor("citation")]).state == "changed"


def test_legacy_github_entry_uses_the_skill_hash():
    e = entry(source_type="github", content_hash=SKILL_HASH, metadata={})
    assert classify_one(e, [contributor("citation")]).state == "matches"
    other = contributor("citation", content_hash="sha256:" + "cd" * 32)
    assert classify_one(e, [other]).state == "changed"


def test_local_entry_uses_the_contributor_hash():
    e = entry(source_type="local", content_hash=SKILL_HASH)
    assert classify_one(e, [contributor("citation")]).state == "matches"
    drifted = contributor("citation", content_hash="sha256:" + "cd" * 32)
    assert classify_one(e, [drifted]).state == "changed"


def test_missing_and_unreadable(monkeypatch):
    assert classify_one(entry(), []).state == "missing"

    def boom(c):
        raise OSError("gone")

    monkeypatch.setattr(content, "collect_files", boom)
    assert classify_one(entry(), [contributor("citation")]).state == "unreadable"


def test_mcp_entries_are_unchecked():
    e = entry(name="papers", kind="mcp", source_type="github")
    assert classify_one(e, [contributor("papers", kind="mcp_tool")]).state == "unchecked"


def test_name_normalization_matches(collected):
    st = classify_one(entry(name="my-skill"), [contributor("My Skill")])
    assert st.state == "matches"


def test_duplicate_names_hash_tiebreak(monkeypatch):
    stale = contributor("citation", id="/tmp/a")
    fresh = contributor("citation", id="/tmp/b")
    monkeypatch.setattr(content, "collect_files",
        lambda c: list(FILES) if c.id == "/tmp/b" else
        [{"path": "SKILL.md", "data": b"old\n", "executable": False}])
    st = classify_one(entry(), [stale, fresh])
    assert st.state == "matches"
    assert st.contributor.id == "/tmp/b"
    assert st.note is None


def test_duplicate_names_without_a_match_note_ambiguity(monkeypatch):
    a = contributor("citation", id="/tmp/a")
    b = contributor("citation", id="/tmp/b")
    monkeypatch.setattr(content, "collect_files",
        lambda c: [{"path": "SKILL.md", "data": c.id.encode(), "executable": False}])
    st = classify_one(entry(), [a, b])
    assert st.state == "changed"
    assert st.contributor.id == "/tmp/a"
    assert st.note and "share this name" in st.note
