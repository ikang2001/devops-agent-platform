# RAG And Knowledge Governance

Status: **governance draft only**. There is no embedding, vector index, or
historical-incident retrieval implementation under `src/`. Current RCA knowledge
retrieval is relational Runbook search by tenant and service name.

## Knowledge Sources

- Published Runbooks from the relational catalog.
- Historical incident summaries after privacy review.
- RCA reports with reviewer acceptance.
- Operational policy documents approved by platform owners.

## Versioning

Every knowledge snapshot must record:

- source collection
- source revision or content hash
- embedding model and version
- chunking strategy
- index build timestamp
- approval owner
- rollback target

## Permissions

Retrieval must be tenant-scoped. Historical incidents from another tenant may
be used only after anonymization and explicit governance approval.

## Evaluation

Before an index becomes active:

1. Run retrieval evaluation against known incidents.
2. Check top-k hit rate, empty-recall rate, unsafe-source recall, and latency.
3. Verify that no secret-like text appears in retrieved snippets.
4. Roll out through `historical_incident_rag_v1` or a successor flag.

## Failure Mode

If the vector index is unavailable, RCA must continue with metrics, logs,
traces, and Runbooks. The system should mark retrieval as incomplete rather
than inventing historical matches.
