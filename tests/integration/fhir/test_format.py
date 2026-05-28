"""Integration tests for the `_format` query parameter.

FHIR R5 §3.1.2: clients may pass `?_format=<mime-type>` as an alternative
to the `Accept` header. The server returns 406 for unsupported formats.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON

pytestmark = pytest.mark.integration


class TestFormatParam:
    def test_fhir_json_format_passes_through(self, client: httpx.Client) -> None:
        r = client.get("/metadata?_format=application/fhir%2Bjson")
        assert r.status_code == 200
        assert r.json()["resourceType"] == "CapabilityStatement"

    def test_json_shorthand_passes_through(self, client: httpx.Client) -> None:
        r = client.get("/metadata?_format=json")
        assert r.status_code == 200
        assert r.json()["resourceType"] == "CapabilityStatement"

    def test_application_json_passes_through(self, client: httpx.Client) -> None:
        r = client.get("/metadata?_format=application/json")
        assert r.status_code == 200

    def test_xml_format_returns_406(self, client: httpx.Client) -> None:
        r = client.get("/metadata?_format=application/fhir%2Bxml")
        assert r.status_code == 406
        body = r.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "not-supported"

    def test_xml_shorthand_returns_406(self, client: httpx.Client) -> None:
        r = client.get("/metadata?_format=xml")
        assert r.status_code == 406
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_unknown_format_returns_406(self, client: httpx.Client) -> None:
        r = client.get("/metadata?_format=application/pdf")
        assert r.status_code == 406

    def test_406_response_is_fhir_json(self, client: httpx.Client) -> None:
        r = client.get("/metadata?_format=xml")
        assert r.status_code == 406
        assert r.headers["content-type"].startswith(FHIR_JSON)

    def test_no_format_param_unaffected(self, client: httpx.Client) -> None:
        r = client.get("/metadata")
        assert r.status_code == 200

    def test_format_applies_to_protected_routes(self, client: httpx.Client) -> None:
        r = client.get("/Patient?_format=xml")
        assert r.status_code == 406

    def test_format_checked_before_auth(self, client: httpx.Client) -> None:
        # Format negotiation runs before route-level auth: an unauthenticated
        # request with an unsupported _format should get 406, not 401.
        with httpx.Client(
            base_url=str(client.base_url),
            headers={"Accept": FHIR_JSON},
            timeout=10.0,
        ) as bare:
            r = bare.get("/Patient?_format=xml")
        assert r.status_code == 406
