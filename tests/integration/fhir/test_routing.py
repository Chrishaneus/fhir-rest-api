from __future__ import annotations

import httpx
import pytest

from tests.integration.fhir.conftest import FHIR_JSON, assert_operation_outcome

pytestmark = pytest.mark.integration


class TestResourceTypeRouting:
    def test_unknown_type_on_post_returns_404(self, client: httpx.Client) -> None:
        r = client.post(
            "/NotAResource",
            json={"resourceType": "NotAResource"},
            headers={"Content-Type": FHIR_JSON},
        )
        assert r.status_code == 404
        assert_operation_outcome(r)

    def test_unknown_type_on_get_returns_404(self, client: httpx.Client) -> None:
        r = client.get("/NotAResource/some-id")
        assert r.status_code == 404
        assert_operation_outcome(r)
