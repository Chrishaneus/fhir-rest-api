"""Unit tests for transaction and batch bundle processing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def patient(family: str = "Bundle", **extra) -> dict:
    return {"resourceType": "Patient", "name": [{"family": family}], **extra}


def obs(subject_id: str) -> dict:
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
        "subject": {"reference": f"Patient/{subject_id}"},
    }


def _transaction(entries: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


def _batch(entries: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "batch", "entry": entries}


def _post_entry(resource: dict, full_url: str | None = None) -> dict:
    e: dict = {"request": {"method": "POST", "url": resource["resourceType"]}, "resource": resource}
    if full_url:
        e["fullUrl"] = full_url
    return e


def _put_entry(resource: dict, resource_id: str) -> dict:
    rt = resource["resourceType"]
    return {"request": {"method": "PUT", "url": f"{rt}/{resource_id}"}, "resource": {**resource, "id": resource_id}}


def _delete_entry(resource_type: str, resource_id: str) -> dict:
    return {"request": {"method": "DELETE", "url": f"{resource_type}/{resource_id}"}}


def _get_entry(resource_type: str, resource_id: str) -> dict:
    return {"request": {"method": "GET", "url": f"{resource_type}/{resource_id}"}}


# ---------------------------------------------------------------------------
# Top-level validation
# ---------------------------------------------------------------------------


class TestBundleValidation:
    def test_non_bundle_body_returns_400(self, client: TestClient) -> None:
        r = client.post("/", json={"resourceType": "Patient"})
        assert r.status_code == 400

    def test_wrong_bundle_type_returns_400(self, client: TestClient) -> None:
        r = client.post("/", json={"resourceType": "Bundle", "type": "searchset"})
        assert r.status_code == 400

    def test_missing_request_field_returns_400(self, client: TestClient) -> None:
        bundle = _transaction([{"resource": patient()}])  # no "request"
        r = client.post("/", json=bundle)
        assert r.status_code == 400

    def test_unsupported_method_returns_400(self, client: TestClient) -> None:
        bundle = _transaction([{"request": {"method": "PATCH", "url": "Patient/abc"}}])
        r = client.post("/", json=bundle)
        assert r.status_code == 400

    def test_unknown_resource_type_returns_404(self, client: TestClient) -> None:
        bundle = _transaction([_post_entry({"resourceType": "NotAType"})])
        r = client.post("/", json=bundle)
        assert r.status_code == 404

    def test_empty_entry_list_succeeds(self, client: TestClient) -> None:
        r = client.post("/", json=_transaction([]))
        assert r.status_code == 200
        assert r.json()["type"] == "transaction-response"


# ---------------------------------------------------------------------------
# Transaction — success cases
# ---------------------------------------------------------------------------


class TestTransactionSuccess:
    def test_response_type_is_transaction_response(self, client: TestClient) -> None:
        r = client.post("/", json=_transaction([_post_entry(patient())]))
        assert r.json()["type"] == "transaction-response"

    def test_create_entry_returns_201_status(self, client: TestClient) -> None:
        r = client.post("/", json=_transaction([_post_entry(patient())]))
        entry = r.json()["entry"][0]
        assert entry["response"]["status"] == "201 Created"

    def test_create_entry_has_location(self, client: TestClient) -> None:
        r = client.post("/", json=_transaction([_post_entry(patient())]))
        entry = r.json()["entry"][0]
        assert "location" in entry["response"]

    def test_create_entry_has_resource(self, client: TestClient) -> None:
        r = client.post("/", json=_transaction([_post_entry(patient("Tx"))]))
        entry = r.json()["entry"][0]
        assert entry["resource"]["resourceType"] == "Patient"

    def test_create_entry_has_etag(self, client: TestClient) -> None:
        r = client.post("/", json=_transaction([_post_entry(patient())]))
        assert "etag" in r.json()["entry"][0]["response"]

    def test_update_existing_returns_200(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=patient()).json()["id"]
        r = client.post("/", json=_transaction([_put_entry(patient("Updated"), pid)]))
        entry = r.json()["entry"][0]
        assert entry["response"]["status"] == "200 OK"

    def test_upsert_new_id_returns_201(self, client: TestClient) -> None:
        r = client.post("/", json=_transaction([_put_entry(patient(), "brand-new-id-abc")]))
        assert r.json()["entry"][0]["response"]["status"] == "201 Created"

    def test_delete_entry_returns_204(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=patient()).json()["id"]
        r = client.post("/", json=_transaction([_delete_entry("Patient", pid)]))
        assert r.json()["entry"][0]["response"]["status"] == "204 No Content"

    def test_get_entry_returns_200(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=patient()).json()["id"]
        r = client.post("/", json=_transaction([_get_entry("Patient", pid)]))
        entry = r.json()["entry"][0]
        assert entry["response"]["status"] == "200 OK"
        assert entry["resource"]["id"] == pid

    def test_multiple_creates_all_persisted(self, client: TestClient) -> None:
        bundle = _transaction([_post_entry(patient("A")), _post_entry(patient("B"))])
        r = client.post("/", json=bundle)
        assert r.status_code == 200
        entries = r.json()["entry"]
        assert len(entries) == 2
        ids = [e["resource"]["id"] for e in entries]
        assert ids[0] != ids[1]
        # Both actually stored
        for rid in ids:
            assert client.get(f"/Patient/{rid}").status_code == 200

    def test_response_has_one_entry_per_input(self, client: TestClient) -> None:
        bundle = _transaction([_post_entry(patient()), _post_entry(patient())])
        r = client.post("/", json=bundle)
        assert len(r.json()["entry"]) == 2


# ---------------------------------------------------------------------------
# Transaction — atomicity
# ---------------------------------------------------------------------------


class TestTransactionAtomicity:
    def test_failure_rolls_back_preceding_creates(self, client: TestClient) -> None:
        # Second entry references a non-existent resource type → 404 before execution
        bad_entry = {"request": {"method": "POST", "url": "NotARealType"}, "resource": {"resourceType": "NotARealType"}}
        bundle = _transaction([_post_entry(patient("RollbackMe")), bad_entry])
        r = client.post("/", json=bundle)
        assert r.status_code == 404  # whole transaction fails
        # No Patient named "RollbackMe" should have been persisted
        search = client.get("/Patient?name=RollbackMe")
        assert search.json().get("total", 0) == 0

    def test_invalid_resource_body_fails_whole_transaction(self, client: TestClient) -> None:
        # Fails at parse/validation time — nothing reaches the DB
        bad = {"resourceType": "Patient", "nonsenseField": "x"}
        bundle = _transaction([_post_entry(patient()), _post_entry(bad)])
        r = client.post("/", json=bundle)
        assert r.status_code == 422

    def test_execution_time_failure_rolls_back_preceding_db_write(self, client: TestClient) -> None:
        # Entry 2 fails at DB execution time (bad If-Match). Entry 1 reached the DB
        # session but must be rolled back — verifies real transaction atomicity.
        existing_pid = client.post("/Patient", json=patient()).json()["id"]
        entries = [
            _post_entry(patient("RollbackAtExec")),  # entry 1: valid, touches DB
            {  # entry 2: fails at execution time
                "request": {"method": "PUT", "url": f"Patient/{existing_pid}", "ifMatch": 'W/"999"'},
                "resource": {**patient(), "id": existing_pid},
            },
        ]
        r = client.post("/", json=_transaction(entries))
        assert r.status_code == 412
        # Entry 1's write must not have been committed
        assert client.get("/Patient?name=RollbackAtExec").json()["total"] == 0

    def test_version_conflict_returns_412(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=patient()).json()["id"]
        entry = {"request": {"method": "PUT", "url": f"Patient/{pid}", "ifMatch": 'W/"999"'}, "resource": {**patient(), "id": pid}}
        r = client.post("/", json=_transaction([entry]))
        assert r.status_code == 412


# ---------------------------------------------------------------------------
# Batch — success + per-entry errors
# ---------------------------------------------------------------------------


class TestBatchSuccess:
    def test_response_type_is_batch_response(self, client: TestClient) -> None:
        r = client.post("/", json=_batch([_post_entry(patient())]))
        assert r.json()["type"] == "batch-response"

    def test_successful_entry_has_status(self, client: TestClient) -> None:
        r = client.post("/", json=_batch([_post_entry(patient())]))
        assert r.json()["entry"][0]["response"]["status"] == "201 Created"

    def test_all_entries_processed_independently(self, client: TestClient) -> None:
        pid = client.post("/Patient", json=patient()).json()["id"]
        bundle = _batch([
            _post_entry(patient("BatchA")),
            {"request": {"method": "GET", "url": "Patient/does-not-exist-xyz"}},
            _get_entry("Patient", pid),
        ])
        r = client.post("/", json=bundle)
        entries = r.json()["entry"]
        assert len(entries) == 3
        assert entries[0]["response"]["status"] == "201 Created"
        assert entries[1]["response"]["status"] == "404 Not Found"
        assert entries[2]["response"]["status"] == "200 OK"

    def test_error_entry_has_outcome(self, client: TestClient) -> None:
        bundle = _batch([{"request": {"method": "GET", "url": "Patient/no-such-id"}}])
        r = client.post("/", json=bundle)
        error_entry = r.json()["entry"][0]
        assert error_entry["response"]["outcome"]["resourceType"] == "OperationOutcome"

    def test_batch_returns_200_even_with_errors(self, client: TestClient) -> None:
        bundle = _batch([{"request": {"method": "GET", "url": "Patient/nope"}}])
        assert client.post("/", json=bundle).status_code == 200

    def test_successful_creates_are_persisted_despite_other_errors(self, client: TestClient) -> None:
        bundle = _batch([
            _post_entry(patient("BatchPersist")),
            {"request": {"method": "GET", "url": "Patient/no-such-id"}},
        ])
        r = client.post("/", json=bundle)
        created_id = r.json()["entry"][0]["resource"]["id"]
        assert client.get(f"/Patient/{created_id}").status_code == 200


# ---------------------------------------------------------------------------
# Capability statement
# ---------------------------------------------------------------------------


class TestCapabilityBundleTypes:
    def test_transaction_advertised(self, client: TestClient) -> None:
        interactions = client.get("/metadata").json()["rest"][0]["interaction"]
        codes = [i["code"] for i in interactions]
        assert "transaction" in codes

    def test_batch_advertised(self, client: TestClient) -> None:
        interactions = client.get("/metadata").json()["rest"][0]["interaction"]
        codes = [i["code"] for i in interactions]
        assert "batch" in codes
