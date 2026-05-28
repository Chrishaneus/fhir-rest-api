"""Unit tests for response_shaping.py.

Two layers:
  1. Pure function tests — no HTTP, no DB, no fixtures needed.
  2. HTTP-level tests via TestClient — verify routes honour the params end-to-end.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.utils.fhir.response_shaping import (
    apply_elements,
    apply_summary,
    is_count_only,
    shape_bundle,
    shape_resource,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_FULL = {
    "resourceType": "Patient",
    "id": "p1",
    "meta": {"versionId": "1"},
    "name": [{"family": "Smith"}],
    "birthDate": "1990-01-01",
    "gender": "unknown",
    "text": {"status": "generated", "div": "<div>narrative</div>"},
}

_SUBSETTED_CODE = "SUBSETTED"


def _has_subsetted(resource: dict) -> bool:
    return any(
        t.get("code") == _SUBSETTED_CODE
        for t in (resource.get("meta") or {}).get("tag", [])
    )


# ---------------------------------------------------------------------------
# apply_summary
# ---------------------------------------------------------------------------


class TestApplySummaryTrue:
    def test_keeps_only_mandatory_and_text(self) -> None:
        r = apply_summary(_FULL, "true")
        assert set(r.keys()) <= {"id", "meta", "resourceType", "text"}

    def test_removes_clinical_fields(self) -> None:
        r = apply_summary(_FULL, "true")
        assert "name" not in r
        assert "birthDate" not in r
        assert "gender" not in r

    def test_preserves_id(self) -> None:
        assert apply_summary(_FULL, "true")["id"] == "p1"

    def test_adds_subsetted_tag(self) -> None:
        assert _has_subsetted(apply_summary(_FULL, "true"))


class TestApplySummaryText:
    def test_same_fields_as_true(self) -> None:
        r = apply_summary(_FULL, "text")
        assert set(r.keys()) <= {"id", "meta", "resourceType", "text"}

    def test_adds_subsetted_tag(self) -> None:
        assert _has_subsetted(apply_summary(_FULL, "text"))


class TestApplySummaryData:
    def test_removes_text(self) -> None:
        r = apply_summary(_FULL, "data")
        assert "text" not in r

    def test_keeps_clinical_fields(self) -> None:
        r = apply_summary(_FULL, "data")
        assert "name" in r
        assert "birthDate" in r
        assert "gender" in r

    def test_keeps_mandatory_fields(self) -> None:
        r = apply_summary(_FULL, "data")
        assert r["id"] == "p1"
        assert r["resourceType"] == "Patient"

    def test_adds_subsetted_tag(self) -> None:
        assert _has_subsetted(apply_summary(_FULL, "data"))


class TestApplySummaryFalse:
    def test_returns_resource_unchanged(self) -> None:
        r = apply_summary(_FULL, "false")
        assert r == _FULL

    def test_no_subsetted_tag(self) -> None:
        assert not _has_subsetted(apply_summary(_FULL, "false"))


class TestApplySummaryCount:
    def test_returns_resource_unchanged(self) -> None:
        r = apply_summary(_FULL, "count")
        assert r == _FULL


class TestApplySummaryEdgeCases:
    def test_case_insensitive(self) -> None:
        assert set(apply_summary(_FULL, "TRUE").keys()) <= {"id", "meta", "resourceType", "text"}
        assert "text" not in apply_summary(_FULL, "DATA")

    def test_whitespace_trimmed(self) -> None:
        assert set(apply_summary(_FULL, "  true  ").keys()) <= {"id", "meta", "resourceType", "text"}

    def test_no_meta_in_source(self) -> None:
        resource = {"resourceType": "Patient", "id": "x", "name": [{"family": "A"}]}
        r = apply_summary(resource, "true")
        assert _has_subsetted(r)

    def test_existing_tags_preserved(self) -> None:
        resource = {**_FULL, "meta": {"tag": [{"code": "OTHER"}]}}
        r = apply_summary(resource, "true")
        codes = [t["code"] for t in r["meta"]["tag"]]
        assert "OTHER" in codes
        assert "SUBSETTED" in codes

    def test_subsetted_not_duplicated(self) -> None:
        r1 = apply_summary(_FULL, "true")
        r2 = apply_summary(r1, "true")
        tags = r2["meta"]["tag"]
        assert sum(1 for t in tags if t.get("code") == "SUBSETTED") == 1

    def test_does_not_mutate_input(self) -> None:
        original_meta = dict(_FULL["meta"])
        apply_summary(_FULL, "true")
        assert _FULL["meta"] == original_meta


# ---------------------------------------------------------------------------
# apply_elements
# ---------------------------------------------------------------------------


class TestApplyElements:
    def test_keeps_requested_fields(self) -> None:
        r = apply_elements(_FULL, ["name", "birthDate"])
        assert "name" in r
        assert "birthDate" in r

    def test_removes_unrequested_fields(self) -> None:
        r = apply_elements(_FULL, ["name"])
        assert "birthDate" not in r
        assert "gender" not in r
        assert "text" not in r

    def test_always_keeps_mandatory_fields(self) -> None:
        r = apply_elements(_FULL, ["name"])
        assert "id" in r
        assert "meta" in r
        assert "resourceType" in r

    def test_adds_subsetted_tag(self) -> None:
        assert _has_subsetted(apply_elements(_FULL, ["name"]))

    def test_empty_elements_list_keeps_only_mandatory(self) -> None:
        r = apply_elements(_FULL, [])
        assert set(r.keys()) == {"id", "meta", "resourceType"}

    def test_does_not_mutate_input(self) -> None:
        original_keys = set(_FULL.keys())
        apply_elements(_FULL, ["name"])
        assert set(_FULL.keys()) == original_keys


# ---------------------------------------------------------------------------
# shape_resource (combined params)
# ---------------------------------------------------------------------------


class TestShapeResource:
    def test_no_params_returns_unchanged(self) -> None:
        assert shape_resource(_FULL, {}) == _FULL

    def test_summary_param_applied(self) -> None:
        r = shape_resource(_FULL, {"_summary": ["data"]})
        assert "text" not in r

    def test_elements_param_applied(self) -> None:
        r = shape_resource(_FULL, {"_elements": ["name"]})
        assert "birthDate" not in r
        assert "name" in r

    def test_summary_applied_before_elements(self) -> None:
        # _summary=data removes text; _elements=text,name then keeps only name + mandatory
        r = shape_resource(_FULL, {"_summary": ["data"], "_elements": ["name"]})
        assert "text" not in r
        assert "name" in r

    def test_elements_csv_parsed(self) -> None:
        r = shape_resource(_FULL, {"_elements": ["name,birthDate"]})
        assert "name" in r
        assert "birthDate" in r

    def test_elements_multiple_values(self) -> None:
        r = shape_resource(_FULL, {"_elements": ["name", "birthDate"]})
        assert "name" in r
        assert "birthDate" in r

    def test_last_summary_value_wins(self) -> None:
        r = shape_resource(_FULL, {"_summary": ["true", "false"]})
        assert r == _FULL


# ---------------------------------------------------------------------------
# is_count_only
# ---------------------------------------------------------------------------


class TestIsCountOnly:
    def test_true_when_count(self) -> None:
        assert is_count_only({"_summary": ["count"]})

    def test_false_when_true(self) -> None:
        assert not is_count_only({"_summary": ["true"]})

    def test_false_when_absent(self) -> None:
        assert not is_count_only({})

    def test_last_value_wins(self) -> None:
        assert not is_count_only({"_summary": ["count", "false"]})
        assert is_count_only({"_summary": ["false", "count"]})

    def test_case_insensitive(self) -> None:
        assert is_count_only({"_summary": ["COUNT"]})


# ---------------------------------------------------------------------------
# shape_bundle
# ---------------------------------------------------------------------------


def _make_bundle(resources: list[dict]) -> dict:
    entries = [{"fullUrl": f"/{r['resourceType']}/{r['id']}", "resource": r} for r in resources]
    return {"resourceType": "Bundle", "type": "searchset", "total": len(resources), "entry": entries}


class TestShapeBundle:
    def test_no_params_returns_unchanged(self) -> None:
        bundle = _make_bundle([_FULL])
        result = shape_bundle(bundle, {})
        assert result["entry"][0]["resource"] == _FULL

    def test_shapes_each_entry_resource(self) -> None:
        p2 = {**_FULL, "id": "p2"}
        bundle = _make_bundle([_FULL, p2])
        result = shape_bundle(bundle, {"_summary": ["data"]})
        for entry in result["entry"]:
            assert "text" not in entry["resource"]

    def test_entry_envelope_preserved(self) -> None:
        bundle = _make_bundle([_FULL])
        result = shape_bundle(bundle, {"_summary": ["data"]})
        assert result["entry"][0]["fullUrl"] == f"/{_FULL['resourceType']}/{_FULL['id']}"

    def test_entries_without_resource_passed_through(self) -> None:
        bundle: dict = {
            "resourceType": "Bundle",
            "entry": [{"fullUrl": "/x", "search": {"mode": "outcome"}}],
        }
        result = shape_bundle(bundle, {"_summary": ["data"]})
        assert result["entry"][0] == {"fullUrl": "/x", "search": {"mode": "outcome"}}

    def test_count_removes_all_entries(self) -> None:
        bundle = _make_bundle([_FULL])
        result = shape_bundle(bundle, {"_summary": ["count"]})
        assert "entry" not in result

    def test_count_preserves_total(self) -> None:
        bundle = _make_bundle([_FULL])
        result = shape_bundle(bundle, {"_summary": ["count"]})
        assert result["total"] == 1

    def test_count_preserves_bundle_metadata(self) -> None:
        bundle = _make_bundle([_FULL])
        result = shape_bundle(bundle, {"_summary": ["count"]})
        assert result["resourceType"] == "Bundle"
        assert result["type"] == "searchset"

    def test_does_not_mutate_input_bundle(self) -> None:
        bundle = _make_bundle([_FULL])
        original_resource = dict(bundle["entry"][0]["resource"])
        shape_bundle(bundle, {"_summary": ["data"]})
        assert bundle["entry"][0]["resource"] == original_resource


# ---------------------------------------------------------------------------
# HTTP-level tests via TestClient
# ---------------------------------------------------------------------------


def patient(**extra) -> dict:
    return {"resourceType": "Patient", "name": [{"family": "ShapeUnit"}], **extra}


class TestHttpSummaryTrue:
    def test_read_strips_clinical_fields(self, client: TestClient) -> None:
        rid = client.post("/Patient", json=patient()).json()["id"]
        r = client.get(f"/Patient/{rid}?_summary=true")
        assert r.status_code == 200
        body = r.json()
        assert "name" not in body
        assert set(body.keys()) <= {"id", "meta", "resourceType", "text"}

    def test_search_strips_entry_resources(self, client: TestClient) -> None:
        rid = client.post("/Patient", json=patient()).json()["id"]
        r = client.get(f"/Patient?_id={rid}&_summary=true")
        entry_resource = r.json()["entry"][0]["resource"]
        assert "name" not in entry_resource

    def test_vread_strips_clinical_fields(self, client: TestClient) -> None:
        rid = client.post("/Patient", json=patient()).json()["id"]
        r = client.get(f"/Patient/{rid}/_history/1?_summary=true")
        assert r.status_code == 200
        assert "name" not in r.json()


class TestHttpSummaryData:
    def test_read_removes_text(self, client: TestClient) -> None:
        p = patient(text={"status": "generated", "div": "<div>x</div>"})
        rid = client.post("/Patient", json=p).json()["id"]
        r = client.get(f"/Patient/{rid}?_summary=data")
        assert "text" not in r.json()
        assert "name" in r.json()


class TestHttpSummaryCount:
    def test_search_omits_entries(self, client: TestClient) -> None:
        client.post("/Patient", json=patient())
        r = client.get("/Patient?_summary=count")
        assert r.status_code == 200
        body = r.json()
        assert "entry" not in body
        assert "total" in body

    def test_post_search_omits_entries(self, client: TestClient) -> None:
        client.post("/Patient", json=patient())
        r = client.post("/Patient/_search", data={"_summary": "count"})
        assert r.status_code == 200
        assert "entry" not in r.json()


class TestHttpElements:
    def test_read_keeps_only_requested_and_mandatory(self, client: TestClient) -> None:
        rid = client.post("/Patient", json=patient(gender="unknown")).json()["id"]
        r = client.get(f"/Patient/{rid}?_elements=name")
        body = r.json()
        assert "name" in body
        assert "gender" not in body
        assert "id" in body
        assert "resourceType" in body

    def test_search_shapes_entries(self, client: TestClient) -> None:
        rid = client.post("/Patient", json=patient(gender="unknown")).json()["id"]
        r = client.get(f"/Patient?_id={rid}&_elements=name")
        resource = r.json()["entry"][0]["resource"]
        assert "name" in resource
        assert "gender" not in resource

    def test_subsetted_tag_present(self, client: TestClient) -> None:
        rid = client.post("/Patient", json=patient()).json()["id"]
        r = client.get(f"/Patient/{rid}?_elements=name")
        assert _has_subsetted(r.json())
