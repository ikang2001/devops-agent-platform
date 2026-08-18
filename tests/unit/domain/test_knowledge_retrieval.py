from datetime import UTC, datetime

import pytest

from devops_agent_platform.application.services.knowledge_service import (
    InMemoryKnowledgeRetriever,
    KnowledgeQuery,
    KnowledgeService,
)
from devops_agent_platform.domain.models.knowledge import (
    KnowledgeDocument,
    KnowledgeDocumentType,
)


def document(document_id: str, status=None) -> KnowledgeDocument:
    kwargs = {}
    if status is not None:
        kwargs["review_status"] = status
        kwargs["published_at"] = datetime.now(UTC)
    return KnowledgeDocument(
        document_id=document_id,
        tenant_id="tenant-a",
        document_type=KnowledgeDocumentType.KNOWN_ERROR,
        title="Payment timeout",
        service_name="payment-service",
        body="PAYMENT_GATEWAY_ERROR timeout fingerprint",
        error_fingerprint="PAYMENT_GATEWAY_ERROR",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_unpublished_documents_are_not_retrievable_and_tenant_isolated():
    retriever = InMemoryKnowledgeRetriever()
    service = KnowledgeService(retriever)
    await retriever.save(document("draft"))
    published = await service.publish(document("published"), datetime.now(UTC))
    assert published.document_id == "published"
    results = await service.search(
        "tenant-a",
        KnowledgeQuery(
            service="payment-service", error_fingerprint="PAYMENT_GATEWAY_ERROR"
        ),
    )
    assert [item.document.document_id for item in results] == ["published"]
    assert (
        await service.search("tenant-b", KnowledgeQuery(service="payment-service"))
        == ()
    )
