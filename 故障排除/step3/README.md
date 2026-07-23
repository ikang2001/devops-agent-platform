# DevOps Intelligent Troubleshooting Agent Platform

This repository is a Step 3 production skeleton for a DevOps troubleshooting
Agent platform.

The current implementation intentionally keeps business success paths disabled.
Only the following infrastructure behavior is usable:

- `GET /healthz`
- trace id middleware with `X-Trace-Id`
- unified success/error response envelope
- HTTP routes and application service contracts for alert ingestion and RCA
  startup

## Architecture Boundary

- `interfaces`: HTTP DTOs, routers, middleware, exception handlers.
- `application`: use-case commands and orchestration services.
- `domain`: pure domain models, enums, and application exceptions.
- `ports`: outbound contracts consumed by application services.
- `infrastructure`: config, database placeholders, logging context, adapters.
- `agent`: Agent workflow execution skeleton.
- `tools`: tool definition, registry, permission, and execution skeleton.

## Step 3 Business Rule

`POST /api/v1/alerts` and `POST /api/v1/incidents/{incident_id}/rca` must return
HTTP 501 until Step 4 implements real business logic.

This avoids fake success data and keeps the boundary clear for junior engineers:
the skeleton is runnable, but the production workflow is not implemented yet.

## Local Development

Install in editable mode with development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run tests:

```bash
python -m pytest
```

Run the API locally:

```bash
python -m uvicorn main:app --reload
```

