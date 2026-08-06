# Production Release Runbook

## Scope

This runbook covers API and background worker releases for the DevOps Agent
platform. It assumes the image has already passed CI, SBOM, and vulnerability
scan gates.

## Pre-Release Checklist

1. Confirm the release image digest, Git revision, and migration head.
2. Confirm PostgreSQL backup completion and restore drill recency.
3. Confirm Kafka Topic, Consumer group, and DLQ ownership.
4. Confirm OIDC issuer, audience, JWKS URL, and certificate chain.
5. Confirm Alertmanager, Grafana, and Runbook links are reachable.
6. Keep RCA and ticket-submission consumers disabled until migration and
   readiness are green.

## Release Order

1. Apply ConfigMap and Secret updates.
2. Run the migration job:

   ```powershell
   kubectl -n devops-agent apply -f .\ops\deploy\kubernetes\migration-job.yaml
   kubectl -n devops-agent wait --for=condition=complete job/devops-agent-platform-migrate --timeout=180s
   ```

3. Deploy the API:

   ```powershell
   kubectl -n devops-agent apply -f .\ops\deploy\kubernetes\deployment.yaml
   kubectl -n devops-agent rollout status deployment/devops-agent-platform --timeout=180s
   ```

4. Verify:

   ```powershell
   kubectl -n devops-agent get pods -l app.kubernetes.io/name=devops-agent-platform
   kubectl -n devops-agent port-forward svc/devops-agent-platform 8000:80
   curl http://localhost:8000/healthz
   curl http://localhost:8000/readyz
   ```

5. Enable background workers in this order after API readiness is stable:
   Outbox, RCA Consumer, ticket submission Consumer, audit retention.

## Rollback

1. If the migration has not changed irreversible data semantics, roll back the
   Deployment to the previous image:

   ```powershell
   kubectl -n devops-agent rollout undo deployment/devops-agent-platform
   ```

2. If a data migration introduced incompatible state, restore the rehearsed
   database backup to a new instance and redirect traffic only after validation.
3. Disable consumers before rollback when message processing semantics changed.
4. Record image digest, database revision, Kafka offsets, and reason for
   rollback in the incident timeline.

## Post-Release Evidence

- GitHub Release assets include the wheel, source distribution, image
  `sbom.spdx.json`, and `SHA256SUMS`; the checksum file covers all three payloads.
- `/readyz` snapshot.
- Grafana dashboard screenshot or link.
- k6 summary for the release capacity tier.
- Alembic revision.
- Consumer lag and DLQ growth after 15 minutes.
- Outbox backlog age and worker success timestamp.
