from pathlib import Path

from drskill.models import (
    BrokenSymlink,
    Contributor,
    Deployment,
    Finding,
    Provenance,
    RawInstance,
    TokenCost,
)


def make_contributor(**overrides):
    defaults = dict(
        id="/tmp/x/SKILL.md",
        name="x",
        scope="project",
        token_cost=TokenCost(catalog_tokens=10, body_tokens=100),
        content_hash="sha256:abc",
    )
    defaults.update(overrides)
    return Contributor(**defaults)


def test_contributor_defaults():
    c = make_contributor()
    assert c.kind == "skill"
    assert c.source.kind == "unmanaged"
    assert c.deployments == []
    assert c.frontmatter_valid is True


def test_deployment_shadow_field():
    d = Deployment(
        harness="claude-code",
        path=Path("/tmp/x/SKILL.md"),
        scope="project",
        via_symlink=False,
        order=0,
    )
    assert d.shadowed_by is None


def test_finding_round_trip():
    f = Finding(
        check_id="name-shadow",
        severity="warning",
        contributors=["/a", "/b"],
        contributor_names=["a", "b"],
        harnesses=["claude-code"],
        message="b shadows a",
        fingerprint="sha256:def",
    )
    assert f.fix_commands == []
    assert Finding.model_validate(f.model_dump()) == f


def test_raw_instance_and_broken_symlink():
    r = RawInstance(
        harness="pi", scope="user",
        skill_file=Path("/tmp/s/SKILL.md"), via_symlink=True, order=1,
    )
    b = BrokenSymlink(harness="pi", path=Path("/tmp/dead"))
    assert r.order == 1 and b.harness == "pi"
