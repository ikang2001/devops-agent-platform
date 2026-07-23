# Step 5 Production Deployment Assets

This directory contains deployment assets for production rehearsal and platform
handoff. They are examples, not a substitute for the target-environment
acceptance evidence in `STEP4_ACCEPTANCE.md`.

## Local Production Rehearsal

```powershell
docker compose -f .\ops\deploy\docker-compose.yml build app
docker compose -f .\ops\deploy\docker-compose.yml --profile migrate run --rm migrate
docker compose -f .\ops\deploy\docker-compose.yml up -d app
```

Use Podman with the same compose file when Docker Desktop is not available:

```powershell
podman compose -f .\ops\deploy\docker-compose.yml up -d
```

`env.production.example` is intentionally non-secret. Replace placeholder
values with secret-manager references in real deployments.

## Kubernetes

The manifests under `kubernetes/` define:

- namespace and service account
- config map and example secret boundary
- migration job for `alembic upgrade head`
- non-root API deployment with read-only filesystem
- service, HPA, PDB, and default-deny network policy

Apply order for staging:

```powershell
kubectl apply -f .\ops\deploy\kubernetes\namespace.yaml
kubectl apply -f .\ops\deploy\kubernetes\serviceaccount.yaml
kubectl apply -f .\ops\deploy\kubernetes\configmap.yaml
kubectl apply -f .\ops\deploy\kubernetes\secret.example.yaml
kubectl apply -f .\ops\deploy\kubernetes\migration-job.yaml
kubectl apply -f .\ops\deploy\kubernetes\deployment.yaml
kubectl apply -f .\ops\deploy\kubernetes\service.yaml
kubectl apply -f .\ops\deploy\kubernetes\hpa.yaml
kubectl apply -f .\ops\deploy\kubernetes\pdb.yaml
kubectl apply -f .\ops\deploy\kubernetes\networkpolicy.yaml
```

Before production, replace `secret.example.yaml` with a platform secret
manager integration such as External Secrets, Vault Agent, SealedSecrets, or
cloud-native secret injection.
