# Backup, Restore, And Migration Rehearsal

## PostgreSQL Backup

Production deployment must use platform-managed backups. Before each release:

1. Record current Alembic revision.
2. Record latest successful backup time and retention policy.
3. Restore the latest backup to a disposable database.
4. Run `python -m alembic upgrade head` against the restored copy.
5. Run the live PostgreSQL acceptance tests against the restored copy.

## Restore Drill

Minimum restore evidence:

- backup identifier
- restore start and end timestamp
- restored database endpoint
- Alembic revision before and after migration
- row counts for alerts, incidents, workflow runs, Outbox, tickets, and reports
- validation command output

## Migration Rollback Boundary

Alembic downgrade scripts are not a safe production rollback by themselves.
For incompatible schema or data changes, restore to a new database instance and
switch traffic after readiness and smoke tests pass.

## Kafka Recovery Notes

For each release, record:

- source Topic retention and partitions
- RCA and ticket Consumer group IDs
- DLQ Topic names and retention
- offsets before enabling consumers
- replay procedure owner
