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
        # `status` joined the contract with the confirm gate — a manifest is a
        # draft until a human approves it.
        assert set(body) == {"manifest_id", "status", "agents", "graph"}
        assert body["manifest_id"].startswith("mf_")
        assert body["status"] == "draft"
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


class TestConfirmGate:
    """POST /manifest/{id}/confirm — the human trust boundary."""

    def test_ingest_produces_a_draft(self, client, demo):
        url, _ = demo
        assert ingest(client, url).json()["status"] == "draft"

    def test_confirming_returns_a_new_confirmed_manifest(self, client, demo):
        url, _ = demo
        draft = ingest(client, url).json()["manifest_id"]

        response = client.post(f"/manifest/{draft}/confirm", json={"agents": []})
        assert response.status_code == 200

        body = response.json()
        assert body["status"] == "confirmed"
        assert body["manifest_id"] != draft, "confirming must not mutate the draft"

    def test_the_draft_survives_confirmation(self, client, demo):
        """Both sides of the trust boundary stay readable and diffable."""
        url, _ = demo
        draft = ingest(client, url).json()["manifest_id"]
        confirmed = client.post(f"/manifest/{draft}/confirm", json={"agents": []}).json()

        assert client.get(f"/manifest/{draft}").json()["status"] == "draft"
        assert client.get(f"/manifest/{confirmed['manifest_id']}").json()["status"] == "confirmed"

    def test_confirming_with_no_edits_keeps_the_discovered_agents(self, client, demo):
        url, path = demo
        drafted = ingest(client, url, (path / "constraints.yaml").read_text()).json()
        confirmed_id = client.post(
            f"/manifest/{drafted['manifest_id']}/confirm", json={"agents": []}
        ).json()["manifest_id"]

        assert client.get(f"/manifest/{confirmed_id}").json()["agents"] == drafted["agents"]

    def test_confirming_applies_edits_from_the_review_screen(self, client, demo):
        url, path = demo
        drafted = ingest(client, url, (path / "constraints.yaml").read_text()).json()

        edited = [dict(a) for a in drafted["agents"]]
        for agent in edited:
            if agent["id"] == "refund":
                agent["constraints"] = {**agent["constraints"], "max_refund_usd": 25}

        confirmed_id = client.post(
            f"/manifest/{drafted['manifest_id']}/confirm", json={"agents": edited}
        ).json()["manifest_id"]

        stored = client.get(f"/manifest/{confirmed_id}").json()
        refund = next(a for a in stored["agents"] if a["id"] == "refund")
        assert refund["constraints"]["max_refund_usd"] == 25

        # The draft still carries what discovery actually inferred.
        original = client.get(f"/manifest/{drafted['manifest_id']}").json()
        assert next(a for a in original["agents"] if a["id"] == "refund")[
            "constraints"
        ]["max_refund_usd"] == 100

    def test_confirming_is_idempotent(self, client, demo):
        url, _ = demo
        draft = ingest(client, url).json()["manifest_id"]
        first = client.post(f"/manifest/{draft}/confirm", json={"agents": []}).json()
        second = client.post(f"/manifest/{draft}/confirm", json={"agents": []}).json()
        assert first["manifest_id"] == second["manifest_id"]

    def test_different_edits_produce_different_confirmed_manifests(self, client, demo):
        url, path = demo
        drafted = ingest(client, url, (path / "constraints.yaml").read_text()).json()
        draft_id = drafted["manifest_id"]

        first = client.post(f"/manifest/{draft_id}/confirm", json={"agents": []}).json()
        edited = [dict(a) for a in drafted["agents"]]
        edited[0] = {**edited[0], "purpose": "Edited purpose"}
        second = client.post(f"/manifest/{draft_id}/confirm", json={"agents": edited}).json()

        assert first["manifest_id"] != second["manifest_id"]

    def test_confirming_an_unknown_manifest_is_404(self, client):
        response = client.post("/manifest/mf_nope/confirm", json={"agents": []})
        assert response.status_code == 404


class TestAskParse:
    """POST /ask/parse — plain language in, a proposed task out."""

    def confirmed_manifest(self, client, demo) -> str:
        url, path = demo
        draft = ingest(client, url, (path / "constraints.yaml").read_text()).json()["manifest_id"]
        return client.post(f"/manifest/{draft}/confirm", json={"agents": []}).json()["manifest_id"]

    def ask(self, client, manifest_id, text):
        return client.post("/ask/parse", json={"manifest_id": manifest_id, "text": text})

    def test_returns_the_contracted_shape(self, client, demo):
        manifest_id = self.confirmed_manifest(client, demo)
        response = self.ask(client, manifest_id, "issue a refund for invoice 4482")
        assert response.status_code == 200

        body = response.json()
        assert set(body) == {"agent_id", "task", "confidence"}
        assert set(body["task"]) == {"title", "description", "priority", "expectation_criteria"}

    def test_routes_a_refund_request_to_the_refund_agent(self, client, demo):
        manifest_id = self.confirmed_manifest(client, demo)
        body = self.ask(client, manifest_id, "Customer was charged twice, issue a refund").json()
        assert body["agent_id"] == "refund"
        assert body["confidence"] == "high"

    def test_routes_a_classification_request_to_triage(self, client, demo):
        manifest_id = self.confirmed_manifest(client, demo)
        body = self.ask(client, manifest_id, "Classify this incoming request").json()
        assert body["agent_id"] == "triage"

    def test_never_proposes_an_agent_the_contract_gates(self, client, demo):
        """escalation is direct_assignable: false, so /ask must not route to it."""
        manifest_id = self.confirmed_manifest(client, demo)
        body = self.ask(client, manifest_id, "escalate this to a human, create a ticket").json()
        assert body["agent_id"] != "escalation"
        assert body["confidence"] == "low", "an ungated match should not look confident"

    def test_surfaces_the_contract_as_expectation_criteria(self, client, demo):
        manifest_id = self.confirmed_manifest(client, demo)
        body = self.ask(client, manifest_id, "refund invoice 4482").json()
        assert "Refund amount ≤ $100, per contract" in body["task"]["expectation_criteria"]

    def test_extracts_explicit_requirements_from_the_request(self, client, demo):
        manifest_id = self.confirmed_manifest(client, demo)
        body = self.ask(
            client, manifest_id, "Issue a refund. Must respond within 2 minutes."
        ).json()
        assert any("within 2 minutes" in c for c in body["task"]["expectation_criteria"])

    def test_detects_priority(self, client, demo):
        manifest_id = self.confirmed_manifest(client, demo)
        urgent = self.ask(client, manifest_id, "refund this urgently").json()
        normal = self.ask(client, manifest_id, "refund this invoice").json()
        assert urgent["task"]["priority"] == "high"
        assert normal["task"]["priority"] == "medium"

    def test_a_draft_manifest_is_rejected(self, client, demo):
        """A draft governs nothing, so nothing may be assigned against it."""
        url, _ = demo
        draft = ingest(client, url).json()["manifest_id"]
        response = self.ask(client, draft, "refund invoice 4482")
        assert response.status_code == 409
        assert response.json()["code"] == "manifest_not_confirmed"

    def test_an_unknown_manifest_is_404(self, client):
        assert self.ask(client, "mf_nope", "anything").status_code == 404
