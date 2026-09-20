"""ManifestSubscription / ManifestIndex: the trust boundary from the brief
("a draft manifest governs nothing"), plus resilience to a source that
errors or returns nothing.

Added after a live integration test against a real, running
rosterd-ingestion found a draft manifest was silently accepted and made
live -- see manifest_source.py's module docstring for the full story.
"""
from __future__ import annotations

from manifest import AgentManifestEntry, ManifestDocument, ManifestIndex, ManifestStatus
from manifest_source import IngestionPollManifestSource, ManifestSubscription, StaticManifestSource


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


class TestIngestionPollScalingDefault:
    """Ingestion has no `scaling` field at all yet (manifest.py's own
    module docstring), so every entry it returns hits ScalingPolicy()'s
    hardcoded min=1/max=1 floor -- confirmed live against a real running
    kernel: agent_metrics kept recording current_replicas=1,
    desired_replicas=1 no matter how much load /simulate-load pushed
    through it, because desired is clamped to max_replicas=1 regardless of
    queue depth. That silently broke the autoscaling half of the demo
    (Federation's flash-sale button, the Monitor screen) for any manifest
    that came from a real ingest. IngestionPollManifestSource now applies
    the kernel's own configured default scaling policy to every entry it
    fetches, standing in until ingestion can express a real per-agent one.
    """

    class _FakeSettings:
        manifest_id = "mf_real"
        ingestion_url = "http://ingestion.invalid"
        default_min_replicas = 1
        default_max_replicas = 5
        default_target_concurrency = 2

    def test_fetch_overrides_the_hardcoded_scaling_default(self, monkeypatch):
        """Ingestion's real response never includes `scaling` -- Pydantic
        fills AgentManifestEntry.scaling with ScalingPolicy()'s min=1/max=1
        the moment model_validate runs, before IngestionPollManifestSource
        even sees it. This asserts fetch() replaces that with the kernel's
        configured default rather than leaving it pinned at max=1."""
        raw_body = {
            "manifest_id": "mf_real",
            "status": "confirmed",
            "agents": [
                {"id": "fulfillment", "node": "fulfillment", "purpose": "x", "direct_assignable": True}
            ],
        }

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return raw_body

        def fake_get(url, timeout=None):
            return FakeResponse()

        import manifest_source

        monkeypatch.setattr(manifest_source.httpx, "get", fake_get)

        source = IngestionPollManifestSource(self._FakeSettings())
        document = source.fetch()

        assert document is not None
        entry = document.agents[0]
        assert entry.scaling.min_replicas == 1
        assert entry.scaling.max_replicas == 5
        assert entry.scaling.target_concurrency == 2

    def test_no_manifest_id_still_returns_none(self):
        class NoManifestSettings(self._FakeSettings):
            manifest_id = None

        assert IngestionPollManifestSource(NoManifestSettings()).fetch() is None


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
