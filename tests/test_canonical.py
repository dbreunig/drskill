import json
from pathlib import Path

from drskill import service

FIXTURES = Path(__file__).parent / "fixtures" / "manifests"
RAILS_HASH = "sha256:f6d5415881682c9cc3a911eb849b9a583d68f036a71635e8afb08be35658f6cc"


def load_manifest():
    return json.loads((FIXTURES / "basic.json").read_text())


def test_matches_the_rails_canonicalizer_byte_for_byte():
    canonical, runtime_hash = service.canonical_manifest(load_manifest())
    assert canonical == (FIXTURES / "basic.canonical.json").read_text()
    assert runtime_hash == RAILS_HASH


def test_key_order_independent():
    manifest = load_manifest()
    shuffled = dict(reversed(list(manifest.items())))
    assert service.canonical_manifest(shuffled) == service.canonical_manifest(manifest)


def test_drops_a_client_supplied_runtime_hash():
    manifest = load_manifest()
    tagged = {**manifest, "runtime_hash": "sha256:" + "0" * 64}
    assert service.canonical_manifest(tagged) == service.canonical_manifest(manifest)


def test_preserves_unicode_unescaped():
    canonical, _ = service.canonical_manifest({"name": "café"})
    assert '"café"' in canonical


def test_service_error_carries_details():
    err = service.ServiceError("revision_invalid", "bad", details={"manifest": ["msg"]})
    assert err.details == {"manifest": ["msg"]}
    assert service.ServiceError("x", "y").details is None
