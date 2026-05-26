from fastapi.testclient import TestClient

from app.utils.fhir.constants import FHIR_JSON


def patient(family: str = "Smith", **extra) -> dict:
    return {
        "resourceType": "Patient",
        "name": [{"family": family, "given": ["Alex"]}],
        "gender": "unknown",
        **extra,
    }


def test_capability_statement_is_available(client: TestClient) -> None:
    response = client.get("/metadata")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(FHIR_JSON)
    body = response.json()
    assert body["resourceType"] == "CapabilityStatement"
    assert body["fhirVersion"] == "5.0.0"
    assert any(resource["type"] == "Patient" for resource in body["rest"][0]["resource"])


def test_invalid_payload_resource_type_returns_400(client: TestClient) -> None:
    response = client.post("/Patient", json={"resourceType": "Observation", "status": "final"})

    assert response.status_code == 400
    body = response.json()
    assert body["resourceType"] == "OperationOutcome"


def test_unknown_resource_type_returns_404(client: TestClient) -> None:
    response = client.post("/NotAResource", json={"resourceType": "NotAResource"})

    assert response.status_code == 404
    body = response.json()
    assert body["resourceType"] == "OperationOutcome"


def test_fhir_resources_rejects_unknown_field_with_422(client: TestClient) -> None:
    response = client.post(
        "/Patient",
        json={"resourceType": "Patient", "nonsenseField": "x"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["resourceType"] == "OperationOutcome"
    assert "nonsenseField" in body["issue"][0]["diagnostics"]


def test_capability_advertises_all_r5_resource_types(client: TestClient) -> None:
    response = client.get("/metadata")

    assert response.status_code == 200
    body = response.json()
    advertised = {entry["type"] for entry in body["rest"][0]["resource"]}
    for expected in (
        "Patient",
        "Observation",
        "Encounter",
        "MedicationRequest",
        "DiagnosticReport",
        "Bundle",
    ):
        assert expected in advertised
    assert len(advertised) >= 150


def test_create_and_read_resource_with_fhir_headers(client: TestClient) -> None:
    create_response = client.post("/Patient", json=patient("CreateRead"))

    assert create_response.status_code == 201
    assert create_response.headers["etag"] == 'W/"1"'
    assert "last-modified" in create_response.headers
    assert create_response.headers["location"].endswith("/_history/1")

    created = create_response.json()
    assert created["resourceType"] == "Patient"
    assert created["id"]
    assert created["meta"]["versionId"] == "1"
    assert created["meta"]["lastUpdated"]

    read_response = client.get(f"/Patient/{created['id']}")
    assert read_response.status_code == 200
    assert read_response.headers["etag"] == 'W/"1"'
    assert read_response.json()["id"] == created["id"]


def test_update_enforces_url_id_and_etag(client: TestClient) -> None:
    created = client.post("/Patient", json=patient("Update")).json()
    resource_id = created["id"]

    stale_response = client.put(
        f"/Patient/{resource_id}",
        headers={"If-Match": 'W/"999"'},
        json={**created, "active": True},
    )
    assert stale_response.status_code == 412
    assert stale_response.json()["resourceType"] == "OperationOutcome"

    update_response = client.put(
        f"/Patient/{resource_id}",
        headers={"If-Match": 'W/"1"'},
        json={**created, "active": True},
    )
    assert update_response.status_code == 200
    assert update_response.headers["etag"] == 'W/"2"'
    assert update_response.json()["meta"]["versionId"] == "2"


def test_search_returns_bundle_searchset(client: TestClient) -> None:
    created = client.post("/Patient", json=patient("SearchOnly")).json()

    response = client.get("/Patient?family=SearchOnly")

    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "searchset"
    assert body["total"] >= 1
    assert any(entry["resource"]["id"] == created["id"] for entry in body["entry"])


def test_vread_history_and_delete(client: TestClient) -> None:
    created = client.post("/Patient", json=patient("History")).json()
    resource_id = created["id"]
    updated = {**created, "active": False}
    client.put(f"/Patient/{resource_id}", json=updated)

    vread_response = client.get(f"/Patient/{resource_id}/_history/1")
    assert vread_response.status_code == 200
    assert vread_response.json()["meta"]["versionId"] == "1"

    history_response = client.get(f"/Patient/{resource_id}/_history")
    assert history_response.status_code == 200
    assert history_response.json()["type"] == "history"
    assert history_response.json()["total"] == 2

    delete_response = client.delete(f"/Patient/{resource_id}")
    assert delete_response.status_code == 204

    read_deleted_response = client.get(f"/Patient/{resource_id}")
    assert read_deleted_response.status_code == 410
    assert read_deleted_response.json()["resourceType"] == "OperationOutcome"


def test_pagination_count_and_links(client: TestClient) -> None:
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


def test_pagination_invalid_params(client: TestClient) -> None:
    assert client.get("/Patient?_count=bad").status_code == 400
    assert client.get("/Patient?_offset=bad").status_code == 400


def test_prefer_return_minimal(client: TestClient) -> None:
    resp = client.post(
        "/Patient",
        json=patient("Minimal"),
        headers={"Prefer": "return=minimal"},
    )
    assert resp.status_code == 201
    assert resp.content == b""


def test_prefer_return_operation_outcome(client: TestClient) -> None:
    resp = client.post(
        "/Patient",
        json=patient("OOPrefer"),
        headers={"Prefer": "return=OperationOutcome"},
    )
    assert resp.status_code == 201
    assert resp.json()["resourceType"] == "OperationOutcome"


def test_conditional_read_if_none_match_returns_304(client: TestClient) -> None:
    created = client.post("/Patient", json=patient("Cond")).json()
    resource_id = created["id"]

    resp = client.get(f"/Patient/{resource_id}", headers={"If-None-Match": 'W/"1"'})
    assert resp.status_code == 304

    resp2 = client.get(f"/Patient/{resource_id}", headers={"If-None-Match": 'W/"999"'})
    assert resp2.status_code == 200


def test_post_type_search(client: TestClient) -> None:
    created = client.post("/Patient", json=patient("PostSearch")).json()

    resp = client.post("/Patient/_search", data={"family": "PostSearch"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "searchset"
    assert any(e["resource"]["id"] == created["id"] for e in body["entry"])


def test_system_history(client: TestClient) -> None:
    client.post("/Patient", json=patient("SysHist"))

    resp = client.get("/_history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "history"
    assert body["total"] >= 1


def test_type_history(client: TestClient) -> None:
    client.post("/Patient", json=patient("TypeHist"))

    resp = client.get("/Patient/_history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "history"
    assert body["total"] >= 1


def test_bool_search_active(client: TestClient) -> None:
    active = client.post("/Patient", json=patient("Active", active=True)).json()
    inactive = client.post("/Patient", json=patient("Inactive", active=False)).json()

    ids_true = {e["resource"]["id"] for e in client.get("/Patient?active=true").json()["entry"]}
    assert active["id"] in ids_true
    assert inactive["id"] not in ids_true

    ids_false = {e["resource"]["id"] for e in client.get("/Patient?active=false").json()["entry"]}
    assert inactive["id"] in ids_false
    assert active["id"] not in ids_false


def test_validation_error_message_is_readable(client: TestClient) -> None:
    resp = client.post("/Patient", json={"resourceType": "Patient", "badField": "x"})
    assert resp.status_code == 422
    diag = resp.json()["issue"][0]["diagnostics"]
    assert "badField" in diag
    assert "Extra inputs" in diag
    assert "pydantic.dev" not in diag
