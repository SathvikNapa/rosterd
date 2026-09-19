"""ManifestSubscription / ManifestIndex: the trust boundary from the brief
("a draft manifest governs nothing"), plus resilience to a source that
errors or returns nothing.

Added after a live integration test against a real, running
rosterd-ingestion found a draft manifest was silently accepted and made
live -- see manifest_source.py's module docstring for the full story.
"""
from __future__ import annotations

from manifest import AgentManifestEntry, ManifestDocument, ManifestIndex, ManifestStatus
from manifest_source import ManifestSubscription, StaticManifestSource


def make_document(status: ManifestStatus, manifest_id: str = "mf_1") -> ManifestDocument:
    return ManifestDocument(
        manifest_id=manifest_id,
        status=status,
        agents=[AgentManifestEntry(id="refund", node="refund_node", purpose="x", direct_assignable=True)],
    )


class TestTrustBoundary:
    def test_a_draft_manifest_is_refused(self):
        index = ManifestIndex()
        source = StaticManifestSource(make_document(ManifestStatus.draft))
        subscription = ManifestSubscription(source, index, interval_sec=9999)

        assert subscription.poll_once() is False
        assert index.is_loaded() is False
        assert index.get("refund") is None

    def test_a_confirmed_manifest_is_loaded(self):
        index = ManifestIndex()
        source = StaticManifestSource(make_document(ManifestStatus.confirmed))
        subscription = ManifestSubscription(source, index, interval_sec=9999)

        assert subscription.poll_once() is True
        assert index.is_loaded() is True
        assert index.get("refund") is not None

    def test_a_later_draft_does_not_displace_an_already_confirmed_manifest(self):
        """Once confirmed and loaded, a subsequent poll landing on a draft
        (e.g. someone re-ingested and hasn't confirmed the new one yet)
        must not clear what's already live."""
        index = ManifestIndex()
        source = StaticManifestSource(make_document(ManifestStatus.confirmed, "mf_confirmed"))
        subscription = ManifestSubscription(source, index, interval_sec=9999)
        subscription.poll_once()
        assert index.manifest_id == "mf_confirmed"

        source.set(make_document(ManifestStatus.draft, "mf_new_draft"))
        subscription.poll_once()
        assert index.manifest_id == "mf_confirmed"  # unchanged
        assert index.get("refund") is not None


class TestResilience:
    def test_no_document_yet_is_a_harmless_no_op(self):
        index = ManifestIndex()
        subscription = ManifestSubscription(StaticManifestSource(None), index, interval_sec=9999)
        assert subscription.poll_once() is False
        assert index.is_loaded() is False

    def test_a_source_that_raises_does_not_propagate(self):
        class ExplodingSource:
            def fetch(self):
                raise RuntimeError("ingestion is down")

        index = ManifestIndex()
        subscription = ManifestSubscription(ExplodingSource(), index, interval_sec=9999)
        assert subscription.poll_once() is False
        assert index.is_loaded() is False
