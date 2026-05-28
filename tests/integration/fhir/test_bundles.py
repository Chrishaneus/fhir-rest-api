from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers shared by transaction / batch tests
# ---------------------------------------------------------------------------


def _uid(prefix: str = "tx") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _patient(family: str | None = None) -> dict:
    return {"resourceType": "Patient", "name": [{"family": family or _uid("TxPat")}]}


def _post_entry(resource: dict) -> dict:
    return {"request": {"method": "POST", "url": resource["resourceType"]}, "resource": resource}


def _put_entry(resource: dict, resource_id: str) -> dict:
    rt = resource["resourceType"]
    return {"request": {"method": "PUT", "url": f"{rt}/{resource_id}"}, "resource": {**resource, "id": resource_id}}


def _delete_entry(resource_type: str, resource_id: str) -> dict:
    return {"request": {"method": "DELETE", "url": f"{resource_type}/{resource_id}"}}


def _get_entry(resource_type: str, resource_id: str) -> dict:
    return {"request": {"method": "GET", "url": f"{resource_type}/{resource_id}"}}


def _transaction(entries: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


def _batch(entries: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "batch", "entry": entries}


def _post(client: httpx.Client, bundle: dict) -> httpx.Response:
    return client.post("/", headers={"Content-Type": FHIR_JSON}, json=bundle)


class TestSearchBundle:
    def test_search_returns_well_formed_bundle(self, client: httpx.Client) -> None:
        r = client.get("/Patient")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"
        assert "total" in bundle
        assert "timestamp" in bundle
        assert any(link["relation"] == "self" for link in bundle["link"])

    def test_pagination_next_link_present_when_results_exceed_count(
        self, client: httpx.Client
    ) -> None:
        for _ in range(2):
            client.post(
                "/Patient",
                headers={"Content-Type": FHIR_JSON},
                json={"resourceType": "Patient", "name": [{"family": f"Page-{uuid.uuid4().hex[:8]}"}]},
            )
        r = client.get("/Patient?_count=1")
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["total"] >= 2
        assert any(link["relation"] == "next" for link in bundle["link"])


class TestHistoryBundle:
    def test_system_history_returns_history_bundle(self, client: httpx.Client) -> None:
        r = client.get("/_history")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "history"
        assert any(link["relation"] == "self" for link in bundle["link"])

    def test_resource_history_returns_history_bundle(
        self, client: httpx.Client, created_patient: dict
    ) -> None:
        r = client.get(f"/Patient/{created_patient['id']}/_history")
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["type"] == "history"
        assert bundle["total"] >= 1


# ---------------------------------------------------------------------------
# Transaction bundle
# ---------------------------------------------------------------------------


class TestTransactionBundle:
    def test_empty_transaction_returns_200(self, client: httpx.Client) -> None:
        r = _post(client, _transaction([]))
        assert r.status_code == 200
        assert r.json()["type"] == "transaction-response"

    def test_create_resource_is_stored(self, client: httpx.Client) -> None:
        family = _uid("TxCreate")
        r = _post(client, _transaction([_post_entry(_patient(family))]))
        assert r.status_code == 200
        created_id = r.json()["entry"][0]["resource"]["id"]
        assert client.get(f"/Patient/{created_id}").json()["name"][0]["family"] == family

    def test_create_entry_has_201_status_location_etag(self, client: httpx.Client) -> None:
        r = _post(client, _transaction([_post_entry(_patient())]))
        resp = r.json()["entry"][0]["response"]
        assert resp["status"] == "201 Created"
        assert "location" in resp
        assert resp["etag"].startswith('W/"')

    def test_update_existing_resource(self, client: httpx.Client) -> None:
        pid = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient()).json()["id"]
        new_family = _uid("TxUpdated")
        r = _post(client, _transaction([_put_entry(_patient(new_family), pid)]))
        assert r.status_code == 200
        assert r.json()["entry"][0]["response"]["status"] == "200 OK"
        assert client.get(f"/Patient/{pid}").json()["name"][0]["family"] == new_family

    def test_upsert_creates_new_resource(self, client: httpx.Client) -> None:
        new_id = _uid("upsert")
        r = _post(client, _transaction([_put_entry(_patient(), new_id)]))
        assert r.json()["entry"][0]["response"]["status"] == "201 Created"
        assert client.get(f"/Patient/{new_id}").status_code == 200

    def test_delete_resource(self, client: httpx.Client) -> None:
        pid = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient()).json()["id"]
        r = _post(client, _transaction([_delete_entry("Patient", pid)]))
        assert r.json()["entry"][0]["response"]["status"] == "204 No Content"
        assert client.get(f"/Patient/{pid}").status_code == 410

    def test_get_resource(self, client: httpx.Client) -> None:
        pid = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient()).json()["id"]
        r = _post(client, _transaction([_get_entry("Patient", pid)]))
        entry = r.json()["entry"][0]
        assert entry["response"]["status"] == "200 OK"
        assert entry["resource"]["id"] == pid

    def test_multiple_operations_all_committed(self, client: httpx.Client) -> None:
        family_a, family_b = _uid("TxA"), _uid("TxB")
        r = _post(client, _transaction([_post_entry(_patient(family_a)), _post_entry(_patient(family_b))]))
        assert r.status_code == 200
        ids = [e["resource"]["id"] for e in r.json()["entry"]]
        assert ids[0] != ids[1]
        for rid in ids:
            assert client.get(f"/Patient/{rid}").status_code == 200

    def test_invalid_body_rolls_back_all_entries(self, client: httpx.Client) -> None:
        # Fails at parse/validation time — verifies nothing is written before execution begins
        family = _uid("TxRollback")
        bad = {"resourceType": "Patient", "nonsenseField": "x"}
        r = _post(client, _transaction([_post_entry(_patient(family)), _post_entry(bad)]))
        assert r.status_code == 422
        assert client.get(f"/Patient?name={family}").json()["total"] == 0

    def test_execution_time_failure_rolls_back_preceding_db_write(self, client: httpx.Client) -> None:
        # Fails at DB execution time (VersionConflictError on entry 2) — verifies that
        # entry 1's write, which reached the DB session, is actually rolled back.
        existing_pid = client.post(
            "/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient()
        ).json()["id"]
        family = _uid("TxExecRollback")
        entries = [
            _post_entry(_patient(family)),  # entry 1: valid, would succeed alone
            {  # entry 2: fails at execution time — wrong If-Match
                "request": {"method": "PUT", "url": f"Patient/{existing_pid}", "ifMatch": 'W/"999"'},
                "resource": {**_patient(), "id": existing_pid},
            },
        ]
        r = _post(client, _transaction(entries))
        assert r.status_code == 412
        # Entry 1's Patient must NOT be in the DB — real DB-level rollback
        assert client.get(f"/Patient?name={family}").json()["total"] == 0

    def test_version_conflict_returns_412(self, client: httpx.Client) -> None:
        pid = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient()).json()["id"]
        entry = {
            "request": {"method": "PUT", "url": f"Patient/{pid}", "ifMatch": 'W/"999"'},
            "resource": {**_patient(_uid("Conflict")), "id": pid},
        }
        assert _post(client, _transaction([entry])).status_code == 412

    def test_non_bundle_body_returns_400(self, client: httpx.Client) -> None:
        r = _post(client, {"resourceType": "Patient"})
        assert r.status_code == 400
        assert r.json()["resourceType"] == "OperationOutcome"


# ---------------------------------------------------------------------------
# Batch bundle
# ---------------------------------------------------------------------------


class TestBatchBundle:
    def test_single_create_returns_batch_response(self, client: httpx.Client) -> None:
        r = _post(client, _batch([_post_entry(_patient())]))
        assert r.status_code == 200
        assert r.json()["type"] == "batch-response"

    def test_created_resource_is_stored(self, client: httpx.Client) -> None:
        family = _uid("BatchStored")
        r = _post(client, _batch([_post_entry(_patient(family))]))
        created_id = r.json()["entry"][0]["resource"]["id"]
        assert client.get(f"/Patient/{created_id}").status_code == 200

    def test_mixed_success_and_error_entries(self, client: httpx.Client) -> None:
        pid = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient()).json()["id"]
        r = _post(client, _batch([
            _post_entry(_patient()),
            _get_entry("Patient", "no-such-id-xyz"),
            _get_entry("Patient", pid),
        ]))
        assert r.status_code == 200
        entries = r.json()["entry"]
        assert entries[0]["response"]["status"] == "201 Created"
        assert entries[1]["response"]["status"] == "404 Not Found"
        assert entries[2]["response"]["status"] == "200 OK"

    def test_error_entry_has_operation_outcome(self, client: httpx.Client) -> None:
        r = _post(client, _batch([_get_entry("Patient", "no-such-id")]))
        outcome = r.json()["entry"][0]["response"]["outcome"]
        assert outcome["resourceType"] == "OperationOutcome"

    def test_batch_always_returns_200(self, client: httpx.Client) -> None:
        r = _post(client, _batch([_get_entry("Patient", "no-such-id")]))
        assert r.status_code == 200

    def test_successful_creates_persisted_despite_other_failures(self, client: httpx.Client) -> None:
        family = _uid("BatchPersist")
        r = _post(client, _batch([
            _post_entry(_patient(family)),
            _get_entry("Patient", "no-such-id"),
        ]))
        created_id = r.json()["entry"][0]["resource"]["id"]
        assert client.get(f"/Patient/{created_id}").status_code == 200

    def test_delete_via_batch(self, client: httpx.Client) -> None:
        pid = client.post("/Patient", headers={"Content-Type": FHIR_JSON}, json=_patient()).json()["id"]
        r = _post(client, _batch([_delete_entry("Patient", pid)]))
        assert r.json()["entry"][0]["response"]["status"] == "204 No Content"
        assert client.get(f"/Patient/{pid}").status_code == 410

    def test_response_has_one_entry_per_input(self, client: httpx.Client) -> None:
        bundle = _batch([_post_entry(_patient()), _post_entry(_patient())])
        assert len(_post(client, bundle).json()["entry"]) == 2
