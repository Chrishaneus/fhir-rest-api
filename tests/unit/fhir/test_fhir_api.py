import json
import uuid

import pytest
from fastapi.testclient import TestClient
from httpx import Response

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
        response = client.get("/metadata")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(FHIR_JSON)
        body = response.json()
        assert body["resourceType"] == "CapabilityStatement"
        assert body["fhirVersion"] == "5.0.0"

    def test_advertises_all_r5_resource_types(self, client: TestClient) -> None:
        body = client.get("/metadata").json()
        advertised = {entry["type"] for entry in body["rest"][0]["resource"]}
        for expected in ("Patient", "Observation", "Encounter", "MedicationRequest", "Bundle"):
            assert expected in advertised
        assert len(advertised) >= 150

    def test_declares_patch_format(self, client: TestClient) -> None:
        body = client.get("/metadata").json()
        assert "application/json-patch+json" in body["patchFormat"]


class TestErrors:
    def test_type_mismatch_in_body_returns_400(self, client: TestClient) -> None:
        response = client.post("/Patient", json={"resourceType": "Observation", "status": "final"})
        assert response.status_code == 400
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_unknown_resource_type_returns_404(self, client: TestClient) -> None:
        response = client.post("/NotAResource", json={"resourceType": "NotAResource"})
        assert response.status_code == 404
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_extra_field_returns_422(self, client: TestClient) -> None:
        response = client.post("/Patient", json={"resourceType": "Patient", "nonsenseField": "x"})
        assert response.status_code == 422
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert "nonsenseField" in body["issue"][0]["diagnostics"]

    def test_validation_error_message_is_readable(self, client: TestClient) -> None:
        response = client.post("/Patient", json={"resourceType": "Patient", "badField": "x"})
        assert response.status_code == 422
        diagnostics = response.json()["issue"][0]["diagnostics"]
        assert "badField" in diagnostics
        assert "Extra inputs" in diagnostics
        assert "pydantic.dev" not in diagnostics


class TestCreate:
    def test_returns_201_with_fhir_headers(self, client: TestClient) -> None:
        response = client.post("/Patient", json=patient("Create"))
        assert response.status_code == 201
        assert response.headers["etag"] == 'W/"1"'
        assert "last-modified" in response.headers
        assert response.headers["location"].endswith("/_history/1")
        body = response.json()
        assert body["resourceType"] == "Patient"
        assert body["id"]
        assert body["meta"]["versionId"] == "1"
        assert body["meta"]["lastUpdated"]


class TestRead:
    def test_returns_resource_with_etag(self, client: TestClient, created_patient: dict) -> None:
        response = client.get(f"/Patient/{created_patient['id']}")
        assert response.status_code == 200
        assert response.headers["etag"] == 'W/"1"'
        assert response.json()["id"] == created_patient["id"]


class TestUpdate:
    def test_stale_etag_returns_412(self, client: TestClient, created_patient: dict) -> None:
        response = client.put(
            f"/Patient/{created_patient['id']}",
            headers={"If-Match": 'W/"999"'},
            json={**created_patient, "active": True},
        )
        assert response.status_code == 412
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_valid_update_increments_version(self, client: TestClient, created_patient: dict) -> None:
        response = client.put(
            f"/Patient/{created_patient['id']}",
            headers={"If-Match": 'W/"1"'},
            json={**created_patient, "active": True},
        )
        assert response.status_code == 200
        assert response.headers["etag"] == 'W/"2"'
        assert response.json()["meta"]["versionId"] == "2"


class TestConditionalUpdate:
    def test_no_search_params_returns_400(self, client: TestClient) -> None:
        response = client.put("/Patient", json={"resourceType": "Patient"})
        assert response.status_code == 400
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_no_match_creates_resource(self, client: TestClient) -> None:
        family = f"ConditionalNew-{uuid.uuid4().hex[:6]}"
        response = client.put(f"/Patient?family={family}", json={"resourceType": "Patient", "name": [{"family": family}]})
        assert response.status_code == 201
        assert response.json()["name"][0]["family"] == family

    def test_one_match_updates_resource(self, client: TestClient, created_patient: dict) -> None:
        family = created_patient["name"][0]["family"]
        response = client.put(
            f"/Patient?family={family}",
            json={**created_patient, "active": True},
        )
        assert response.status_code == 200
        assert response.json()["active"] is True
        assert response.json()["meta"]["versionId"] == "2"

    def test_multiple_matches_returns_412(self, client: TestClient) -> None:
        for _ in range(2):
            client.post("/Patient", json=patient("MultiMatch"))
        response = client.put("/Patient?family=MultiMatch", json={"resourceType": "Patient"})
        assert response.status_code == 412
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_conflicting_body_id_returns_400(self, client: TestClient, created_patient: dict) -> None:
        family = created_patient["name"][0]["family"]
        response = client.put(
            f"/Patient?family={family}",
            json={**created_patient, "id": "completely-different-id"},
        )
        assert response.status_code == 400
        assert response.json()["resourceType"] == "OperationOutcome"


class TestConditionalDelete:
    def test_no_search_params_returns_400(self, client: TestClient) -> None:
        response = client.delete("/Patient")
        assert response.status_code == 400
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_no_match_returns_204(self, client: TestClient) -> None:
        response = client.delete("/Patient?family=DoesNotExistXYZ")
        assert response.status_code == 204

    def test_one_match_deletes_resource(self, client: TestClient, created_patient: dict) -> None:
        family = created_patient["name"][0]["family"]
        response = client.delete(f"/Patient?family={family}")
        assert response.status_code == 204
        assert client.get(f"/Patient/{created_patient['id']}").status_code == 410

    def test_multiple_matches_deletes_all(self, client: TestClient) -> None:
        for _ in range(3):
            client.post("/Patient", json=patient("CondDelMulti"))
        response = client.delete("/Patient?family=CondDelMulti")
        assert response.status_code == 204
        remaining = client.get("/Patient?family=CondDelMulti").json()
        assert remaining["total"] == 0


class TestDelete:
    def test_returns_204(self, client: TestClient, created_patient: dict) -> None:
        assert client.delete(f"/Patient/{created_patient['id']}").status_code == 204

    def test_stale_etag_returns_412(self, client: TestClient, created_patient: dict) -> None:
        response = client.delete(
            f"/Patient/{created_patient['id']}",
            headers={"If-Match": 'W/"999"'},
        )
        assert response.status_code == 412
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_matching_etag_deletes_resource(self, client: TestClient, created_patient: dict) -> None:
        response = client.delete(
            f"/Patient/{created_patient['id']}",
            headers={"If-Match": 'W/"1"'},
        )
        assert response.status_code == 204

    def test_read_after_delete_returns_410(self, client: TestClient, created_patient: dict) -> None:
        client.delete(f"/Patient/{created_patient['id']}")
        response = client.get(f"/Patient/{created_patient['id']}")
        assert response.status_code == 410
        assert response.json()["resourceType"] == "OperationOutcome"


class TestHistory:
    def test_vread_returns_specific_version(self, client: TestClient, updated_patient: dict) -> None:
        response = client.get(f"/Patient/{updated_patient['id']}/_history/1")
        assert response.status_code == 200
        assert response.json()["meta"]["versionId"] == "1"

    def test_type_history_has_expected_count(self, client: TestClient, updated_patient: dict) -> None:
        response = client.get(f"/Patient/{updated_patient['id']}/_history")
        assert response.status_code == 200
        assert response.json()["type"] == "history"
        assert response.json()["total"] == 2

    def test_system_history_returns_bundle(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("SysHist"))
        response = client.get("/_history")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "history"
        assert body["total"] >= 1

    def test_type_level_history_returns_bundle(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("TypeHist"))
        response = client.get("/Patient/_history")
        assert response.status_code == 200
        body = response.json()
        assert body["type"] == "history"
        assert body["total"] >= 1

    def test_instance_history_count_limits_entries(self, client: TestClient) -> None:
        body = client.post("/Patient", json=patient("HistPage")).json()
        patient_id = body["id"]
        for i in range(3):
            client.put(f"/Patient/{patient_id}", json={**body, "active": bool(i % 2)})
        response = client.get(f"/Patient/{patient_id}/_history?_count=2")
        assert response.status_code == 200
        bundle = response.json()
        assert bundle["total"] == 4
        assert len(bundle["entry"]) == 2

    def test_instance_history_next_link_present(self, client: TestClient) -> None:
        body = client.post("/Patient", json=patient("HistNext")).json()
        patient_id = body["id"]
        for i in range(3):
            client.put(f"/Patient/{patient_id}", json={**body, "active": bool(i % 2)})
        response = client.get(f"/Patient/{patient_id}/_history?_count=2")
        assert any(link["relation"] == "next" for link in response.json()["link"])

    def test_instance_history_offset_skips_entries(self, client: TestClient) -> None:
        body = client.post("/Patient", json=patient("HistOffset")).json()
        patient_id = body["id"]
        for i in range(3):
            client.put(f"/Patient/{patient_id}", json={**body, "active": bool(i % 2)})
        all_history_response = client.get(f"/Patient/{patient_id}/_history")
        paged_history_response = client.get(f"/Patient/{patient_id}/_history?_count=2&_offset=2")
        assert paged_history_response.json()["total"] == 4
        assert len(paged_history_response.json()["entry"]) == 2
        all_version_ids = [entry["resource"]["meta"]["versionId"] for entry in all_history_response.json()["entry"] if entry.get("resource")]
        paged_version_ids = [entry["resource"]["meta"]["versionId"] for entry in paged_history_response.json()["entry"] if entry.get("resource")]
        assert paged_version_ids == all_version_ids[2:4]

    def test_since_filters_instance_history(self, client: TestClient) -> None:
        body = client.post("/Patient", json=patient("SinceTest")).json()
        patient_id = body["id"]
        client.put(f"/Patient/{patient_id}", json={**body, "active": True})
        response = client.get(f"/Patient/{patient_id}/_history?_since=2099-01-01T00:00:00Z")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_since_invalid_value_returns_400(self, client: TestClient) -> None:
        body = client.post("/Patient", json=patient("SinceBad")).json()
        response = client.get(f"/Patient/{body['id']}/_history?_since=not-a-date")
        assert response.status_code == 400
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_since_filters_type_history(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("SinceType"))
        response = client.get("/Patient/_history?_since=2099-01-01T00:00:00Z")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_since_filters_system_history(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("SinceSys"))
        response = client.get("/_history?_since=2099-01-01T00:00:00Z")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_at_filters_instance_history(self, client: TestClient) -> None:
        body = client.post("/Patient", json=patient("AtTest")).json()
        patient_id = body["id"]
        client.put(f"/Patient/{patient_id}", json={**body, "active": True})
        response = client.get(f"/Patient/{patient_id}/_history?_at=1999-01-01T00:00:00Z")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_at_invalid_value_returns_400(self, client: TestClient) -> None:
        body = client.post("/Patient", json=patient("AtBad")).json()
        response = client.get(f"/Patient/{body['id']}/_history?_at=not-a-date")
        assert response.status_code == 400
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_at_filters_type_history(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("AtType"))
        response = client.get("/Patient/_history?_at=1999-01-01T00:00:00Z")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_at_filters_system_history(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("AtSys"))
        response = client.get("/_history?_at=1999-01-01T00:00:00Z")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_type_history_pagination(self, client: TestClient) -> None:
        for i in range(3):
            client.post("/Patient", json=patient(f"TypePage{i}"))
        response = client.get("/Patient/_history?_count=2")
        assert response.status_code == 200
        bundle = response.json()
        assert bundle["total"] >= 3
        assert len(bundle["entry"]) == 2
        assert any(link["relation"] == "next" for link in bundle["link"])

    def test_system_history_pagination(self, client: TestClient) -> None:
        for i in range(3):
            client.post("/Patient", json=patient(f"SysPage{i}"))
        response = client.get("/_history?_count=2")
        assert response.status_code == 200
        bundle = response.json()
        assert bundle["total"] >= 3
        assert len(bundle["entry"]) == 2
        assert any(link["relation"] == "next" for link in bundle["link"])


class TestSearch:
    def test_by_family_returns_matching_bundle(self, client: TestClient) -> None:
        created = client.post("/Patient", json=patient("SearchOnly")).json()
        response = client.get("/Patient?family=SearchOnly")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "searchset"
        assert body["total"] >= 1
        assert any(entry["resource"]["id"] == created["id"] for entry in body["entry"])

    def test_post_type_search(self, client: TestClient) -> None:
        created = client.post("/Patient", json=patient("PostSearch")).json()
        response = client.post("/Patient/_search", data={"family": "PostSearch"})
        assert response.status_code == 200
        body = response.json()
        assert body["type"] == "searchset"
        assert any(entry["resource"]["id"] == created["id"] for entry in body["entry"])

    def test_total_none_omits_total_field(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("TotalNone"))
        response = client.get("/Patient?family=TotalNone&_total=none")
        assert response.status_code == 200
        assert "total" not in response.json()

    def test_total_accurate_includes_total_field(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("TotalAcc"))
        response = client.get("/Patient?family=TotalAcc&_total=accurate")
        assert response.status_code == 200
        assert response.json()["total"] >= 1

    def test_total_estimate_includes_total_field(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("TotalEst"))
        response = client.get("/Patient?family=TotalEst&_total=estimate")
        assert response.status_code == 200
        assert response.json()["total"] >= 1

    def test_total_invalid_value_returns_400(self, client: TestClient) -> None:
        response = client.get("/Patient?_total=bogus")
        assert response.status_code == 400
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_total_none_on_post_search(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("TotalNonePost"))
        response = client.post("/Patient/_search", data={"family": "TotalNonePost", "_total": "none"})
        assert response.status_code == 200
        assert "total" not in response.json()

    def test_bool_search_active(self, client: TestClient) -> None:
        active = client.post("/Patient", json=patient("Active", active=True)).json()
        inactive = client.post("/Patient", json=patient("Inactive", active=False)).json()

        ids_active = {entry["resource"]["id"] for entry in client.get("/Patient?active=true").json()["entry"]}
        assert active["id"] in ids_active
        assert inactive["id"] not in ids_active

        ids_inactive = {entry["resource"]["id"] for entry in client.get("/Patient?active=false").json()["entry"]}
        assert inactive["id"] in ids_inactive
        assert active["id"] not in ids_inactive


class TestPagination:
    def test_count_and_links(self, client: TestClient) -> None:
        for i in range(5):
            client.post("/Patient", json=patient(f"Page{i}"))

        page1 = client.get("/Patient?_count=3").json()
        assert len(page1["entry"]) == 3
        assert page1["total"] == 5
        links1 = {link_entry["relation"]: link_entry["url"] for link_entry in page1["link"]}
        assert "self" in links1
        assert "next" in links1
        assert "previous" not in links1

        page2 = client.get("/Patient?_count=3&_offset=3").json()
        assert len(page2["entry"]) == 2
        links2 = {link_entry["relation"]: link_entry["url"] for link_entry in page2["link"]}
        assert "previous" in links2
        assert "first" in links2
        assert "next" not in links2

    def test_invalid_params_return_400(self, client: TestClient) -> None:
        assert client.get("/Patient?_count=bad").status_code == 400
        assert client.get("/Patient?_offset=bad").status_code == 400


class TestPreferHeader:
    def test_return_minimal_gives_empty_body(self, client: TestClient) -> None:
        response = client.post("/Patient", json=patient("Minimal"), headers={"Prefer": "return=minimal"})
        assert response.status_code == 201
        assert response.content == b""

    def test_return_operation_outcome(self, client: TestClient) -> None:
        response = client.post("/Patient", json=patient("OOPrefer"), headers={"Prefer": "return=OperationOutcome"})
        assert response.status_code == 201
        assert response.json()["resourceType"] == "OperationOutcome"


class TestConditionalRead:
    def test_current_etag_returns_304(self, client: TestClient, created_patient: dict) -> None:
        response = client.get(f"/Patient/{created_patient['id']}", headers={"If-None-Match": 'W/"1"'})
        assert response.status_code == 304

    def test_stale_etag_returns_200(self, client: TestClient, created_patient: dict) -> None:
        response = client.get(f"/Patient/{created_patient['id']}", headers={"If-None-Match": 'W/"999"'})
        assert response.status_code == 200


class TestRequestId:
    def test_generates_request_id_when_absent(self, client: TestClient) -> None:
        response = client.get("/metadata")
        assert "x-request-id" in response.headers
        assert len(response.headers["x-request-id"]) == 32  # uuid4().hex

    def test_echoes_client_provided_request_id(self, client: TestClient) -> None:
        response = client.get("/metadata", headers={"X-Request-ID": "my-correlation-id"})
        assert response.headers["x-request-id"] == "my-correlation-id"

    def test_request_id_consistent_across_resource_endpoints(self, client: TestClient, created_patient: dict) -> None:
        response = client.get(f"/Patient/{created_patient['id']}", headers={"X-Request-ID": "trace-abc"})
        assert response.headers["x-request-id"] == "trace-abc"


class TestSort:
    @pytest.fixture(autouse=True)
    def _seed(self, client: TestClient) -> None:
        for name in ("Adams", "Carter", "Baker"):
            client.post("/Patient", json=patient(name))

    def _families(self, client: TestClient, query_string: str) -> list[str]:
        response = client.get(f"/Patient{query_string}")
        assert response.status_code == 200
        return [
            entry["resource"]["name"][0]["family"]
            for entry in response.json()["entry"]
            if "resource" in entry
        ]

    def test_sort_ascending_by_family(self, client: TestClient) -> None:
        assert self._families(client, "?_sort=family") == ["Adams", "Baker", "Carter"]

    def test_sort_descending_by_family(self, client: TestClient) -> None:
        assert self._families(client, "?_sort=-family") == ["Carter", "Baker", "Adams"]

    def test_no_sort_returns_results(self, client: TestClient) -> None:
        assert len(self._families(client, "")) == 3

    def test_no_outcome_warning_entry(self, client: TestClient) -> None:
        response = client.get("/Patient?_sort=family")
        outcome_entries = [
            entry for entry in response.json()["entry"] if entry.get("search", {}).get("mode") == "outcome"
        ]
        assert len(outcome_entries) == 0

    def test_post_search_sort(self, client: TestClient) -> None:
        response = client.post("/Patient/_search", data={"_sort": "family"})
        assert response.status_code == 200
        families = [
            entry["resource"]["name"][0]["family"]
            for entry in response.json()["entry"]
            if "resource" in entry
        ]
        assert families == ["Adams", "Baker", "Carter"]


class TestPatch:
    def _patch(self, client: TestClient, resource_id: str, operations: list) -> Response:
        return client.patch(
            f"/Patient/{resource_id}",
            content=json.dumps(operations),
            headers={"Content-Type": "application/json-patch+json"},
        )

    def test_patch_adds_field(self, client: TestClient, created_patient: dict) -> None:
        response = self._patch(client, created_patient["id"], [{"op": "add", "path": "/active", "value": True}])
        assert response.status_code == 200
        assert response.json()["active"] is True

    def test_patch_replaces_field(self, client: TestClient, created_patient: dict) -> None:
        operations = [{"op": "replace", "path": "/name/0/family", "value": "Patched"}]
        response = self._patch(client, created_patient["id"], operations)
        assert response.status_code == 200
        assert response.json()["name"][0]["family"] == "Patched"

    def test_patch_increments_version(self, client: TestClient, created_patient: dict) -> None:
        response = self._patch(client, created_patient["id"], [{"op": "add", "path": "/active", "value": False}])
        assert response.status_code == 200
        assert response.headers["etag"] == 'W/"2"'
        assert response.json()["meta"]["versionId"] == "2"

    def test_patch_wrong_content_type_returns_415(self, client: TestClient, created_patient: dict) -> None:
        response = client.patch(
            f"/Patient/{created_patient['id']}",
            json=[{"op": "add", "path": "/active", "value": True}],
        )
        assert response.status_code == 415
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_patch_nonexistent_returns_404(self, client: TestClient) -> None:
        response = self._patch(client, "doesnotexist", [{"op": "add", "path": "/active", "value": True}])
        assert response.status_code == 404
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_patch_invalid_operation_returns_400(self, client: TestClient, created_patient: dict) -> None:
        response = self._patch(client, created_patient["id"], [{"op": "badop", "path": "/active", "value": True}])
        assert response.status_code == 400
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_patch_stale_etag_returns_412(self, client: TestClient, created_patient: dict) -> None:
        response = client.patch(
            f"/Patient/{created_patient['id']}",
            content=json.dumps([{"op": "add", "path": "/active", "value": True}]),
            headers={
                "Content-Type": "application/json-patch+json",
                "If-Match": 'W/"999"',
            },
        )
        assert response.status_code == 412
        assert response.json()["resourceType"] == "OperationOutcome"


class TestIncludes:
    @pytest.fixture
    def patient_with_observation(self, client: TestClient) -> tuple[dict, dict]:
        patient_resource = client.post("/Patient", json=patient("IncludeTest")).json()
        observation = client.post("/Observation", json={
            "resourceType": "Observation",
            "status": "final",
            "code": {"text": "HR"},
            "subject": {"reference": f"Patient/{patient_resource['id']}"},
        }).json()
        return patient_resource, observation

    def test_include_observation_subject_fetches_patient(
        self, client: TestClient, patient_with_observation: tuple
    ) -> None:
        patient_resource, observation = patient_with_observation
        response = client.get(f"/Observation?_id={observation['id']}&_include=Observation:subject")
        assert response.status_code == 200
        body = response.json()
        entries = body["entry"]
        match_entries = [entry for entry in entries if entry.get("search", {}).get("mode") == "match"]
        include_entries = [entry for entry in entries if entry.get("search", {}).get("mode") == "include"]
        assert len(match_entries) == 1
        assert match_entries[0]["resource"]["resourceType"] == "Observation"
        assert len(include_entries) == 1
        assert include_entries[0]["resource"]["resourceType"] == "Patient"
        assert include_entries[0]["resource"]["id"] == patient_resource["id"]

    def test_include_with_target_type_filter(
        self, client: TestClient, patient_with_observation: tuple
    ) -> None:
        patient_resource, observation = patient_with_observation
        response = client.get(f"/Observation?_id={observation['id']}&_include=Observation:subject:Patient")
        assert response.status_code == 200
        include_entries = [
            entry for entry in response.json()["entry"]
            if entry.get("search", {}).get("mode") == "include"
        ]
        assert len(include_entries) == 1
        assert include_entries[0]["resource"]["id"] == patient_resource["id"]

    def test_include_wrong_target_type_returns_no_includes(
        self, client: TestClient, patient_with_observation: tuple
    ) -> None:
        _, observation = patient_with_observation
        response = client.get(f"/Observation?_id={observation['id']}&_include=Observation:subject:Encounter")
        assert response.status_code == 200
        include_entries = [
            entry for entry in response.json()["entry"]
            if entry.get("search", {}).get("mode") == "include"
        ]
        assert len(include_entries) == 0

    def test_revinclude_observation_subject_appends_observations(
        self, client: TestClient, patient_with_observation: tuple
    ) -> None:
        patient_resource, observation = patient_with_observation
        response = client.get(f"/Patient?_id={patient_resource['id']}&_revinclude=Observation:subject")
        assert response.status_code == 200
        body = response.json()
        entries = body["entry"]
        match_entries = [entry for entry in entries if entry.get("search", {}).get("mode") == "match"]
        include_entries = [entry for entry in entries if entry.get("search", {}).get("mode") == "include"]
        assert len(match_entries) == 1
        assert match_entries[0]["resource"]["resourceType"] == "Patient"
        assert len(include_entries) == 1
        assert include_entries[0]["resource"]["resourceType"] == "Observation"
        assert include_entries[0]["resource"]["id"] == observation["id"]

    def test_no_includes_no_mode_on_plain_search(self, client: TestClient) -> None:
        client.post("/Patient", json=patient("NoInclude"))
        response = client.get("/Patient")
        assert response.status_code == 200
        modes = {entry.get("search", {}).get("mode") for entry in response.json()["entry"]}
        assert modes == {"match"}

    def test_search_mode_match_present_without_include_param(
        self, client: TestClient, created_patient: dict
    ) -> None:
        response = client.get(f"/Patient?_id={created_patient['id']}")
        assert response.status_code == 200
        entries = response.json()["entry"]
        assert all(entry.get("search", {}).get("mode") == "match" for entry in entries)


class TestFormatParam:
    def test_json_format_accepted(self, client: TestClient) -> None:
        response = client.get("/metadata?_format=application/fhir+json")
        assert response.status_code == 200
        assert response.json()["resourceType"] == "CapabilityStatement"

    def test_json_shorthand_accepted(self, client: TestClient) -> None:
        assert client.get("/metadata?_format=json").status_code == 200

    def test_application_json_accepted(self, client: TestClient) -> None:
        assert client.get("/metadata?_format=application/json").status_code == 200

    def test_xml_format_returns_406(self, client: TestClient) -> None:
        response = client.get("/metadata?_format=application/fhir+xml")
        assert response.status_code == 406
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert "application/fhir+xml" in body["issue"][0]["diagnostics"]

    def test_xml_shorthand_returns_406(self, client: TestClient) -> None:
        assert client.get("/Patient?_format=xml").status_code == 406

    def test_unknown_format_returns_406(self, client: TestClient) -> None:
        response = client.get("/metadata?_format=application/pdf")
        assert response.status_code == 406

    def test_no_format_param_unaffected(self, client: TestClient) -> None:
        assert client.get("/metadata").status_code == 200


class TestReferenceValidation:
    def _obs(self, subject_ref: str | None = None, encounter_ref: str | None = None) -> dict:
        body: dict = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"text": "HR"},
        }
        if subject_ref is not None:
            body["subject"] = {"reference": subject_ref}
        if encounter_ref is not None:
            body["encounter"] = {"reference": encounter_ref}
        return body

    def test_valid_reference_accepted(self, client: TestClient, created_patient: dict) -> None:
        response = client.post("/Observation", json=self._obs(f"Patient/{created_patient['id']}"))
        assert response.status_code == 201

    def test_nonexistent_subject_returns_422(self, client: TestClient) -> None:
        response = client.post("/Observation", json=self._obs("Patient/doesnotexist"))
        assert response.status_code == 422
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert "doesnotexist" in body["issue"][0]["diagnostics"]

    def test_absent_subject_field_is_not_required(self, client: TestClient) -> None:
        response = client.post("/Observation", json=self._obs())
        assert response.status_code == 201

    def test_absolute_url_reference_is_skipped(self, client: TestClient) -> None:
        response = client.post(
            "/Observation",
            json=self._obs("https://other-server.example.com/Patient/remote-id"),
        )
        assert response.status_code == 201

    def test_nonexistent_encounter_returns_422(
        self, client: TestClient, created_patient: dict
    ) -> None:
        response = client.post(
            "/Observation",
            json=self._obs(
                f"Patient/{created_patient['id']}",
                encounter_ref="Encounter/doesnotexist",
            ),
        )
        assert response.status_code == 422

    def test_update_validates_changed_reference(
        self, client: TestClient, created_patient: dict
    ) -> None:
        observation = client.post(
            "/Observation", json=self._obs(f"Patient/{created_patient['id']}")
        ).json()
        response = client.put(
            f"/Observation/{observation['id']}",
            json={**observation, "subject": {"reference": "Patient/ghost"}},
        )
        assert response.status_code == 422
        assert response.json()["resourceType"] == "OperationOutcome"

    def test_allergy_intolerance_validates_patient(self, client: TestClient) -> None:
        response = client.post(
            "/AllergyIntolerance",
            json={
                "resourceType": "AllergyIntolerance",
                "patient": {"reference": "Patient/ghost"},
                "code": {"text": "Penicillin"},
            },
        )
        assert response.status_code == 422

    def test_condition_validates_subject(self, client: TestClient) -> None:
        response = client.post(
            "/Condition",
            json={
                "resourceType": "Condition",
                "subject": {"reference": "Patient/ghost"},
                "code": {"text": "Fever"},
            },
        )
        assert response.status_code == 422
