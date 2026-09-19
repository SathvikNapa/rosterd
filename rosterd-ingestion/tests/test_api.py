"""The HTTP contract: POST /ingest and GET /manifest/{manifest_id}."""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import DEMO_AGENT, SIMPLE_AGENT, SIMPLE_CONSTRAINTS


@pytest.fixture
def demo(make_repo):
    """The demo-agent fixture, copied into a throwaway git repo."""
    files = {
        rel: (DEMO_AGENT / rel).read_text()
        for rel in ("agent.py", "tools.py", "langgraph.json", "constraints.yaml")
    }
    return make_repo("demo", files)


def ingest(client, url, constraints=""):
    return client.post("/ingest", json={"repo_url": url, "constraints_yaml": constraints})


class TestIngest:
    def test_returns_the_contracted_shape(self, client, demo):
        url, path = demo
        response = ingest(client, url, (path / "constraints.yaml").read_text())
        assert response.status_code == 200

        body = response.json()
        assert set(body) == {"manifest_id", "agents", "graph"}
        assert body["manifest_id"].startswith("mf_")
        assert set(body["graph"]) == {"nodes", "edges"}

        agent = body["agents"][0]
        assert set(agent) == {
            "id", "node", "purpose", "tools",
            "direct_assignable", "entry_only_via", "constraints",
        }

    def test_produces_the_three_agents_from_the_mockup(self, client, demo):
        url, path = demo
        body = ingest(client, url, (path / "constraints.yaml").read_text()).json()
        by_id = {a["id"]: a for a in body["agents"]}
        assert set(by_id) == {"triage", "refund", "escalation"}
        assert by_id["refund"]["constraints"]["max_refund_usd"] == 100
        assert by_id["escalation"]["direct_assignable"] is False

    def test_falls_back_to_the_repo_constraints_file_when_the_body_is_empty(self, client, demo):
        url, _ = demo
        body = ingest(client, url, "").json()
        refund = next(a for a in body["agents"] if a["id"] == "refund")
        assert refund["constraints"]["max_refund_usd"] == 100

    def test_constraints_in_the_body_win_over_the_repo_file(self, client, demo):
        url, _ = demo
        body = ingest(client, url, "constraints:\n  refund_node:\n    max_refund_usd: 5\n").json()
        refund = next(a for a in body["agents"] if a["id"] == "refund")
        assert refund["constraints"]["max_refund_usd"] == 5

    def test_is_idempotent(self, client, demo):
        url, path = demo
        constraints = (path / "constraints.yaml").read_text()
        first = ingest(client, url, constraints).json()
        second = ingest(client, url, constraints).json()
        assert first["manifest_id"] == second["manifest_id"]
        assert len(client.get("/manifests").json()["manifests"]) == 1

    def test_changed_constraints_yield_a_new_manifest_id(self, client, demo):
        url, _ = demo
        first = ingest(client, url, "constraints:\n  refund_node:\n    max_refund_usd: 100\n").json()
        second = ingest(client, url, "constraints:\n  refund_node:\n    max_refund_usd: 50\n").json()
        assert first["manifest_id"] != second["manifest_id"]
        assert len(client.get("/manifests").json()["manifests"]) == 2

    def test_a_typo_in_a_node_name_is_rejected_with_a_suggestion(self, client, demo):
        url, _ = demo
        response = ingest(client, url, "constraints:\n  refund_nod:\n    max_refund_usd: 100\n")
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "constraints_unknown_nodes"
        assert body["details"]["unknown_nodes"][0]["did_you_mean"] == "refund_node"

    def test_invalid_yaml_is_rejected(self, client, demo):
        url, _ = demo
        response = ingest(client, url, "constraints:\n  a: [unclosed\n")
        assert response.status_code == 422
        assert response.json()["code"] == "constraints_invalid"

    def test_a_repo_with_no_graph_is_rejected(self, client, make_repo):
        url, _ = make_repo("nograph", {"readme.md": "nothing here"})
        response = ingest(client, url)
        assert response.status_code == 422
        assert response.json()["code"] == "graph_not_found"

    def test_an_unreachable_repo_is_rejected(self, client):
        response = ingest(client, "http://localhost/does-not-exist")
        assert response.status_code == 422
        assert response.json()["code"] == "repo_fetch_failed"

    def test_a_non_http_repo_url_is_rejected_by_the_schema(self, client):
        response = ingest(client, "file:///etc/passwd")
        assert response.status_code == 422

    def test_host_allowlist_is_enforced(self, client, demo, monkeypatch):
        monkeypatch.setenv("ROSTERD_ALLOWED_HOSTS", "github.com")
        monkeypatch.delenv("ROSTERD_LOCAL_REPO_ROOT")
        response = ingest(client, "https://evil.example.com/repo")
        assert response.status_code == 403
        assert response.json()["code"] == "repo_not_allowed"


class TestGetManifest:
    def test_returns_the_same_document_as_ingest(self, client, demo):
        url, path = demo
        created = ingest(client, url, (path / "constraints.yaml").read_text()).json()
        fetched = client.get(f"/manifest/{created['manifest_id']}")
        assert fetched.status_code == 200
        assert fetched.json() == created

    def test_does_not_re_clone(self, client, demo, monkeypatch):
        """The whole point of GET: no repo access, so it works offline."""
        url, path = demo
        created = ingest(client, url, (path / "constraints.yaml").read_text()).json()

        def explode(*args, **kwargs):
            raise AssertionError("GET /manifest must not fetch the repo")

        monkeypatch.setattr("repo.fetch_repo", explode)
        assert client.get(f"/manifest/{created['manifest_id']}").status_code == 200

    def test_an_old_version_still_resolves_after_a_re_ingest(self, client, demo):
        """A kernel pinned to v1 keeps working after someone re-ingests."""
        url, _ = demo
        first = ingest(client, url, "constraints:\n  refund_node:\n    max_refund_usd: 100\n").json()
        ingest(client, url, "constraints:\n  refund_node:\n    max_refund_usd: 50\n")

        old = client.get(f"/manifest/{first['manifest_id']}").json()
        refund = next(a for a in old["agents"] if a["id"] == "refund")
        assert refund["constraints"]["max_refund_usd"] == 100

    def test_unknown_id_returns_404(self, client):
        response = client.get("/manifest/mf_nope")
        assert response.status_code == 404
        assert response.json()["code"] == "manifest_not_found"


class TestProvenance:
    def test_exposes_the_hashes_the_contracts_screen_needs(self, client, demo):
        url, path = demo
        created = ingest(client, url, (path / "constraints.yaml").read_text()).json()
        provenance = client.get(f"/manifest/{created['manifest_id']}/provenance").json()

        assert provenance["version"] == 1
        assert provenance["lineage_id"].startswith("ln_")
        assert len(provenance["constraints_sha256"]) == 64
        assert len(provenance["commit_sha"]) == 40
        assert provenance["discovery_mode"] == "import"

    def test_records_the_supersedes_chain(self, client, demo):
        url, _ = demo
        first = ingest(client, url, "constraints:\n  refund_node:\n    max_refund_usd: 100\n").json()
        second = ingest(client, url, "constraints:\n  refund_node:\n    max_refund_usd: 50\n").json()

        second_prov = client.get(f"/manifest/{second['manifest_id']}/provenance").json()
        first_prov = client.get(f"/manifest/{first['manifest_id']}/provenance").json()

        assert second_prov["version"] == 2
        assert second_prov["supersedes"] == first["manifest_id"]
        assert first_prov["superseded_by"] == second["manifest_id"]

    def test_surfaces_ingest_time_warnings(self, client, demo):
        url, _ = demo
        created = ingest(client, url, "constraints:\n  refund_node:\n    direct_assignable: true\n").json()
        provenance = client.get(f"/manifest/{created['manifest_id']}/provenance").json()
        assert any("no constraints block" in w for w in provenance["warnings"])


def test_healthz_reports_effective_settings(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["discovery_mode"] == "import"
