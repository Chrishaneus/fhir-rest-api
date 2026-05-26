import pytest
from fastapi.testclient import TestClient

from app.utils.fhir.constants import FHIR_JSON


def patient(family: str = "Smith", **extra) -> dict:
    return {
        "resourceType": "Patient",
        "name": [{"family": family, "given": ["Alex"]}],
        "gender": "unknown",
        **extra,
    }


@pytest.fixture
def created_patient(client: TestClient) -> dict:
    return client.post("/Patient", json=patient()).json()


@pytest.fixture
def updated_patient(client: TestClient) -> dict:
    body = client.post("/Patient", json=patient("Updated")).json()
    client.put(f"/Patient/{body['id']}", json={**body, "active": False})
    return body


class TestMetadata:
    def test_returns_capability_statement(self, client: TestClient) -> None:
        r = client.get("/metadata")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith(FHIR_JSON)
        body = r.json()
        assert body["resourceType"] == "CapabilityStatement"
        assert body["fhirVersion"] == "5.0.0"

    def test_advertises_all_r5_resource_types(self, client: TestClient) -> None:
        body = client.get("/metadata").json()
        advertised = {entry["type"] for entry in body["rest"][0]["resource"]}
        for expected in ("Patient", "Observation", "Encounter", "MedicationRequest", "Bundle"):
            assert expected in advertised
        assert len(advertised) >= 150


class TestErrors:
    def test_type_mismatch_in_body_returns_400(self, client: TestClient) -> None:
        r = client.post("/Patient", json={"resourceType": "Observation", "status": "final"})
        assert r.status_code == 400
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_unknown_resource_type_returns_404(self, client: TestClient) -> None:
        r = client.post("/NotAResource", json={"resourceType": "NotAResource"})
        assert r.status_code == 404
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_extra_field_returns_422(self, client: TestClient) -> None:
        r = client.post("/Patient", json={"resourceType": "Patient", "nonsenseField": "x"})
        assert r.status_code == 422
        body = r.json()
        assert body["resourceType"] == "OperationOutcome"
        assert "nonsenseField" in body["issue"][0]["diagnostics"]

    def test_validation_error_message_is_readable(self, client: TestClient) -> None:
        r = client.post("/Patient", json={"resourceType": "Patient", "badField": "x"})
        assert r.status_code == 422
        diag = r.json()["issue"][0]["diagnostics"]
        assert "badField" in diag
        assert "Extra inputs" in diag
        assert "pydantic.dev" not in diag


class TestCreate:
    def test_returns_201_with_fhir_headers(self, client: TestClient) -> None:
        r = client.post("/Patient", json=patient("Create"))
        assert r.status_code == 201
        assert r.headers["etag"] == 'W/"1"'
        assert "last-modified" in r.headers
        assert r.headers["location"].endswith("/_history/1")
        body = r.json()
        assert body["resourceType"] == "Patient"
        assert body["id"]
        assert body["meta"]["versionId"] == "1"
        assert body["meta"]["lastUpdated"]


class TestRead:
    def test_returns_resource_with_etag(self, client: TestClient, created_patient: dict) -> None:
        r = client.get(f"/Patient/{created_patient['id']}")
        assert r.status_code == 200
        assert r.headers["etag"] == 'W/"1"'
        assert r.json()["id"] == created_patient["id"]


class TestUpdate:
    def test_stale_etag_returns_412(self, client: TestClient, created_patient: dict) -> None:
        r = client.put(
            f"/Patient/{created_patient['id']}",
            headers={"If-Match": 'W/"999"'},
            json={**created_patient, "active": True},
        )
        assert r.status_code == 412
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_valid_update_increments_version(self, client: TestClient, created_patient: dict) -> None:
        r = client.put(
            f"/Patient/{created_patient['id']}",
            headers={"If-Match": 'W/"1"'},
            json={**created_patient, "active": True},
        )
        assert r.status_code == 200
        assert r.headers["etag"] == 'W/"2"'
        assert r.json()["meta"]["versionId"] == "2"


class TestDelete:
    def test_returns_204(self, client: TestClient, created_patient: dict) -> None:
        assert client.delete(f"/Patient/{created_patient['id']}").status_code == 204

    def test_read_after_delete_returns_410(self, client: TestClient, created_patient: dict) -> None:
        client.delete(f"/Patient/{created_patient['id']}")
        r = client.get(f"/Patient/{created_patient['id']}")
        assert r.status_code == 410
        assert r.json()["resourceType"] == "OperationOutcome"


class TestHistory:
    def test_vread_returns_specific_version(self, client: TestClient, updated_patient: dict) -> None:
        r = client.get(f"/Patient/{updated_patient['id']}/_history/1")
        assert r.status_code == 200
        assert r.json()["meta"]["versionId"] == "1"

    def test_type_history_has_expected_count(self, client: TestClient, updated_patient: dict) -> None:
        r = client.get(f"/Patient/{updated_patient['id']}/_history")
        assert r.status_code == 200
        assert r.json()["type"] == "history"
        assert r.json()["total"] == 2

    def test_system_history_returns_bundle(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("SysHist"))
        r = client.get("/_history")
        assert r.status_code == 200
        body = r.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "history"
        assert body["total"] >= 1

    def test_type_level_history_returns_bundle(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("TypeHist"))
        r = client.get("/Patient/_history")
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "history"
        assert body["total"] >= 1


class TestSearch:
    def test_by_family_returns_matching_bundle(self, client: TestClient) -> None:
        created = client.post("/Patient", json=patient("SearchOnly")).json()
        r = client.get("/Patient?family=SearchOnly")
        assert r.status_code == 200
        body = r.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "searchset"
        assert body["total"] >= 1
        assert any(entry["resource"]["id"] == created["id"] for entry in body["entry"])

    def test_post_type_search(self, client: TestClient) -> None:
        created = client.post("/Patient", json=patient("PostSearch")).json()
        r = client.post("/Patient/_search", data={"family": "PostSearch"})
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "searchset"
        assert any(e["resource"]["id"] == created["id"] for e in body["entry"])

    def test_bool_search_active(self, client: TestClient) -> None:
        active = client.post("/Patient", json=patient("Active", active=True)).json()
        inactive = client.post("/Patient", json=patient("Inactive", active=False)).json()

        ids_true = {e["resource"]["id"] for e in client.get("/Patient?active=true").json()["entry"]}
        assert active["id"] in ids_true
        assert inactive["id"] not in ids_true

        ids_false = {e["resource"]["id"] for e in client.get("/Patient?active=false").json()["entry"]}
        assert inactive["id"] in ids_false
        assert active["id"] not in ids_false


class TestPagination:
    def test_count_and_links(self, client: TestClient) -> None:
        for i in range(5):
            client.post("/Patient", json=patient(f"Page{i}"))

        page1 = client.get("/Patient?_count=3").json()
        assert len(page1["entry"]) == 3
        assert page1["total"] == 5
        links1 = {lnk["relation"]: lnk["url"] for lnk in page1["link"]}
        assert "self" in links1
        assert "next" in links1
        assert "previous" not in links1

        page2 = client.get("/Patient?_count=3&_offset=3").json()
        assert len(page2["entry"]) == 2
        links2 = {lnk["relation"]: lnk["url"] for lnk in page2["link"]}
        assert "previous" in links2
        assert "first" in links2
        assert "next" not in links2

    def test_invalid_params_return_400(self, client: TestClient) -> None:
        assert client.get("/Patient?_count=bad").status_code == 400
        assert client.get("/Patient?_offset=bad").status_code == 400


class TestPreferHeader:
    def test_return_minimal_gives_empty_body(self, client: TestClient) -> None:
        r = client.post("/Patient", json=patient("Minimal"), headers={"Prefer": "return=minimal"})
        assert r.status_code == 201
        assert r.content == b""

    def test_return_operation_outcome(self, client: TestClient) -> None:
        r = client.post("/Patient", json=patient("OOPrefer"), headers={"Prefer": "return=OperationOutcome"})
        assert r.status_code == 201
        assert r.json()["resourceType"] == "OperationOutcome"


class TestConditionalRead:
    def test_current_etag_returns_304(self, client: TestClient, created_patient: dict) -> None:
        r = client.get(f"/Patient/{created_patient['id']}", headers={"If-None-Match": 'W/"1"'})
        assert r.status_code == 304

    def test_stale_etag_returns_200(self, client: TestClient, created_patient: dict) -> None:
        r = client.get(f"/Patient/{created_patient['id']}", headers={"If-None-Match": 'W/"999"'})
        assert r.status_code == 200
