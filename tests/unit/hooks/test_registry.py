"""Tests for the HookRegistry and ResourceHooks base class."""

from typing import Any

from fastapi.testclient import TestClient

from app.hooks import ResourceHooks, hooks
from app.utils.errors import FHIRHTTPError


class RecordingHooks(ResourceHooks):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def before_create(self, resource: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("before_create", resource))
        marked = {**resource, "active": True}
        marked.setdefault("meta", {})
        marked["meta"] = {**marked["meta"], "tag": [{"system": "test", "code": "stamped"}]}
        return marked

    def after_create(self, resource: dict[str, Any]) -> None:
        self.calls.append(("after_create", resource))

    def before_update(self, old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("before_update_old", old))
        self.calls.append(("before_update_new", new))
        return {**new, "active": False}

    def after_update(self, old: dict[str, Any] | None, new: dict[str, Any]) -> None:
        self.calls.append(("after_update", new))

    def before_delete(self, resource: dict[str, Any]) -> None:
        self.calls.append(("before_delete", resource))

    def after_delete(self, resource: dict[str, Any]) -> None:
        self.calls.append(("after_delete", resource))


def test_create_hook_can_mutate_resource(client: TestClient) -> None:
    recorder = RecordingHooks()
    hooks.register("Patient", recorder)

    response = client.post("/Patient", json={"resourceType": "Patient"})

    assert response.status_code == 201
    body = response.json()
    assert body["active"] is True
    assert body["meta"]["tag"] == [{"system": "test", "code": "stamped"}]
    assert [call[0] for call in recorder.calls] == ["before_create", "after_create"]


def test_update_and_delete_hooks_fire_with_old_and_new(client: TestClient) -> None:
    recorder = RecordingHooks()
    hooks.register("Patient", recorder)

    created = client.post("/Patient", json={"resourceType": "Patient", "active": False}).json()
    resource_id = created["id"]

    update_response = client.put(
        f"/Patient/{resource_id}",
        json={"resourceType": "Patient", "id": resource_id, "active": True},
    )
    assert update_response.status_code == 200
    assert update_response.json()["active"] is False

    delete_response = client.delete(f"/Patient/{resource_id}")
    assert delete_response.status_code == 204

    assert [call[0] for call in recorder.calls] == [
        "before_create",
        "after_create",
        "before_update_old",
        "before_update_new",
        "after_update",
        "before_delete",
        "after_delete",
    ]


def test_before_hook_can_reject_with_operation_outcome(client: TestClient) -> None:
    class RejectingHooks(ResourceHooks):
        def before_create(self, resource: dict[str, Any]) -> dict[str, Any]:
            raise FHIRHTTPError(422, "Patients must have at least one name", "business-rule")

    hooks.register("Patient", RejectingHooks())

    response = client.post("/Patient", json={"resourceType": "Patient"})

    assert response.status_code == 422
    body = response.json()
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["diagnostics"] == "Patients must have at least one name"


def test_hook_is_per_resource_type(client: TestClient) -> None:
    recorder = RecordingHooks()
    hooks.register("Patient", recorder)

    response = client.post(
        "/Observation",
        json={"resourceType": "Observation", "status": "final", "code": {"text": "Heart rate"}},
    )

    assert response.status_code == 201
    assert recorder.calls == []
