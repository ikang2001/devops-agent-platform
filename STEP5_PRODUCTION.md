# Step 5 Productionization

Status: **production deployment assets and rehearsal gates added; real staging
sign-off still required before production traffic.**

Step 5 turns the Step 4 backend into a deployable service package. It does not
claim that a real production cluster has been accepted.

## Completed In Repository

- Multi-stage non-root `Dockerfile` with read-only friendly runtime.
- `.dockerignore` that excludes tests, caches, local env files, and secret
  directories from the image build context.
- Production-like local Compose stack under `ops/deploy/docker-compose.yml`.
- Kubernetes examples for namespace, service account, config, secret boundary,
  migration job, deployment, service, HPA, PDB, and NetworkPolicy.
- GitHub Actions workflow for Ruff, tests, Alembic offline migration SQL,
  image build, SBOM generation, and Trivy scan.
- Release, rollback, backup, disaster recovery, and capacity runbooks under
  `ops/deploy`.

## Required Target-Environment Gates

1. Build the exact image digest and store SBOM plus vulnerability scan results.
2. Run `python -m alembic upgrade head` through the migration job against a
   disposable copy of target schema and data.
3. Verify `/readyz` with PostgreSQL, Kafka, OIDC, observability, LLM, and
   ticketing dependencies configured.
4. Run k6 at 1x, 2x, and expected peak traffic.
5. Run deployment-native fault injection for PostgreSQL, Kafka, OIDC/JWKS,
   Prometheus, Loki, Tempo, LLM, and ticketing.
6. Confirm all production secrets are injected by the platform and never
   present in image layers, config maps, logs, metrics labels, DLQ metadata, or
   Outbox payloads.
7. Execute rollback and database restore drills before enabling background
   consumers.

## Production Readiness Boundaries

- `secret.example.yaml` must be replaced by External Secrets, Vault,
  SealedSecrets, or the cloud provider secret manager.
- The application image runs as UID/GID `10001`, drops Linux capabilities, and
  expects no writable filesystem except `/tmp`.
- Kubernetes manifests are intentionally vendor-neutral; ingress, TLS
  certificate issuance, and managed database/Kafka classes belong to the
  deployment platform.
- Local Compose and Podman validation remain engineering rehearsals, not
  target-environment sign-off.
