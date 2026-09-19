"""Tasks 4 and 5: retrieval, and the manifest versioning decision."""
from __future__ import annotations

import pytest

from errors import ManifestNotFoundError
from ingestion import AgentManifestEntry
from shared import GraphSpec
from store import ManifestStore, compute_manifest_id, lineage_id_for


def save(store: ManifestStore, *, commit="c1", constraints="s1", repo="https://example.com/r"):
    manifest_id = compute_manifest_id(repo, commit, constraints)
    return store.save(
        manifest_id=manifest_id,
        agents=[AgentManifestEntry(id="refund", node="refund_node", purpose="Refunds")],
        graph=GraphSpec(nodes=["__start__", "refund_node", "__end__"], edges=[]),
        repo_url=repo,
        commit_sha=commit,
        constraints_sha=constraints,
        discovery_mode="import",
        graph_located_via="test",
        warnings=[],
    )


@pytest.fixture
def store(settings):
    return ManifestStore(settings)


class TestContentAddressing:
    def test_identical_inputs_produce_the_same_id(self):
        a = compute_manifest_id("https://example.com/r", "c1", "s1")
        assert a == compute_manifest_id("https://example.com/r", "c1", "s1")

    def test_a_new_commit_produces_a_new_id(self):
        a = compute_manifest_id("https://example.com/r", "c1", "s1")
        assert a != compute_manifest_id("https://example.com/r", "c2", "s1")

    def test_changed_constraints_produce_a_new_id(self):
        a = compute_manifest_id("https://example.com/r", "c1", "s1")
        assert a != compute_manifest_id("https://example.com/r", "c1", "s2")

    def test_cosmetic_url_differences_share_a_lineage(self):
        assert lineage_id_for("https://example.com/r") == lineage_id_for("https://example.com/r.git/")
        assert lineage_id_for("https://example.com/r") == lineage_id_for("HTTPS://EXAMPLE.COM/r")


class TestVersioning:
    def test_first_ingest_is_version_one(self, store):
        assert save(store).provenance.version == 1

    def test_re_ingesting_identical_content_does_not_create_a_version(self, store):
        first = save(store)
        second = save(store)
        assert second.manifest_id == first.manifest_id
        assert second.provenance.version == 1
        assert len(store.list_all()) == 1

    def test_changed_constraints_create_version_two(self, store):
        first = save(store)
        second = save(store, constraints="s2")
        assert second.provenance.version == 2
        assert second.provenance.supersedes == first.manifest_id

    def test_the_old_manifest_stays_readable_after_being_superseded(self, store):
        """A run pinned to v1 must keep resolving after someone re-ingests."""
        first = save(store)
        second = save(store, constraints="s2")

        still_there = store.get(first.manifest_id)
        assert still_there.manifest_id == first.manifest_id
        assert still_there.provenance.superseded_by == second.manifest_id
        assert still_there.agents == first.agents

    def test_lineage_lists_every_version_in_order(self, store):
        save(store)
        save(store, constraints="s2")
        save(store, constraints="s3")
        lineage = store.lineage(lineage_id_for("https://example.com/r"))
        assert [entry["version"] for entry in lineage] == [1, 2, 3]

    def test_separate_repos_get_separate_lineages(self, store):
        save(store, repo="https://example.com/a")
        second = save(store, repo="https://example.com/b")
        assert second.provenance.version == 1
        assert second.provenance.supersedes is None


class TestRetrieval:
    def test_round_trips_through_disk(self, store):
        saved = save(store)
        loaded = store.get(saved.manifest_id)
        assert loaded.manifest_id == saved.manifest_id
        assert loaded.agents[0].node == "refund_node"
        assert loaded.provenance.commit_sha == "c1"

    def test_a_fresh_store_reads_manifests_written_by_an_earlier_process(self, settings):
        """Restarting the service must not lose manifests."""
        saved = save(ManifestStore(settings))
        assert ManifestStore(settings).get(saved.manifest_id).manifest_id == saved.manifest_id

    def test_unknown_id_raises_not_found(self, store):
        with pytest.raises(ManifestNotFoundError):
            store.get("mf_nope")

    def test_path_traversal_in_an_id_cannot_escape_the_data_dir(self, store):
        with pytest.raises(ManifestNotFoundError):
            store.get("../../../../etc/passwd")
