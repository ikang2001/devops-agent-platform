from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from devops_agent_platform.application.security import AdministratorPrincipal
from devops_agent_platform.application.services.dataset_release_service import (
    DatasetReleaseResult,
)
from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.domain.models.dataset_release import (
    DatasetRelease,
    DatasetReleaseStatus,
)

BASE_URL = "/api/v1/admin/tenants/tenant-a/dataset-releases/release-v2"


class Authenticator:
    async def authenticate(self, bearer_token: str) -> AdministratorPrincipal:
        assert bearer_token == "dataset-token"
        return AdministratorPrincipal(
            admin_id="curator-001",
            scopes=frozenset(
                {
                    "datasets:curate",
                    "datasets:review",
                    "datasets:publish",
                    "datasets:read",
                }
            ),
            tenant_ids=frozenset({"tenant-a"}),
        )


class RecordingService:
    def __init__(self) -> None:
        self.commands = []

    def result(self, trace_id: str) -> DatasetReleaseResult:
        return DatasetReleaseResult(
            DatasetRelease(
                release_id="release-v2",
                tenant_id="tenant-a",
                dataset_id="rca-benchmark",
                source_version="v1",
                version="v2",
                dataset_json=json.dumps(
                    {
                        "dataset_id": "rca-benchmark",
                        "version": "v2",
                        "privacy": "anonymized",
                        "samples": [{"case_id": "case-001"}],
                    }
                ),
                dataset_sha256="c" * 64,
                candidate_sha256="a" * 64,
                curation_review_sha256="b" * 64,
                requested_by="curator-001",
                synthetic=True,
                status=DatasetReleaseStatus.DRAFT,
                revision=1,
                reviews=(),
                created_at=datetime(2026, 8, 18, tzinfo=UTC),
            ),
            trace_id,
            False,
        )

    async def create(self, command):
        self.commands.append(command)
        return self.result(command.trace_id)

    async def review(self, command):
        self.commands.append(command)
        return self.result(command.trace_id)

    async def publish(self, command):
        self.commands.append(command)
        return self.result(command.trace_id)

    async def get(self, tenant_id: str, release_id: str, trace_id: str):
        assert (tenant_id, release_id) == ("tenant-a", "release-v2")
        return self.result(trace_id)


def client() -> tuple[TestClient, RecordingService]:
    app = create_app(runtime_enabled=False, admin_authenticator=Authenticator())
    service = RecordingService()
    app.state.dataset_release_service = service
    return TestClient(app), service


def auth_headers(**extra: str) -> dict[str, str]:
    return {"Authorization": "Bearer dataset-token", **extra}


def test_dataset_release_http_workflow_builds_authenticated_commands() -> None:
    http, service = client()
    create = http.put(
        BASE_URL,
        headers=auth_headers(
            **{"Idempotency-Key": "create-v2", "X-Trace-Id": "trace-create"}
        ),
        json={
            "dataset_id": "rca-benchmark",
            "source_version": "v1",
            "version": "v2",
            "dataset": {
                "dataset_id": "rca-benchmark",
                "version": "v2",
                "privacy": "anonymized",
                "samples": [{"case_id": "case-001"}],
            },
            "candidate_sha256": "a" * 64,
            "curation_review_sha256": "b" * 64,
            "synthetic": True,
        },
    )
    review = http.post(
        f"{BASE_URL}/reviews",
        headers=auth_headers(**{"Idempotency-Key": "review-v2", "If-Match": '"1"'}),
        json={"role": "domain", "approved": True, "notes": "Reviewed."},
    )
    publish = http.post(
        f"{BASE_URL}/publish",
        headers=auth_headers(**{"Idempotency-Key": "publish-v2", "If-Match": '"3"'}),
    )
    fetched = http.get(BASE_URL, headers=auth_headers())

    assert [create.status_code, review.status_code, publish.status_code] == [
        200,
        200,
        200,
    ]
    assert fetched.status_code == 200
    assert service.commands[0].tenant_id == "tenant-a"
    assert service.commands[0].requested_by == "curator-001"
    assert service.commands[1].expected_revision == 1
    assert service.commands[2].expected_revision == 3
