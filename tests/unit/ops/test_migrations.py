import os
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_HEAD = "20260819_0039"


def test_migration_history_is_a_single_linear_chain() -> None:
    """迁移历史必须只有一个 Head，且每个版本只指向前一个版本。"""
    script = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    revisions = list(script.walk_revisions())

    assert script.get_heads() == [EXPECTED_HEAD]
    assert len(revisions) == 39
    assert revisions[-1].down_revision is None
    for revision, parent in zip(
        revisions[:-1],
        revisions[1:],
        strict=True,
    ):
        assert revision.down_revision == parent.revision


def test_postgresql_migration_chain_compiles_offline() -> None:
    """从 Base 到 Head 的 PostgreSQL DDL 必须能在无数据库时完整生成。"""
    environment = os.environ.copy()
    environment["DEVOPS_AGENT_DATABASE_URL"] = (
        "postgresql+psycopg://migration:secret@localhost/devops_agent"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "20260701_0023 -> 20260702_0024" in result.stderr
    assert "20260702_0024 -> 20260702_0025" in result.stderr
    assert "20260702_0025 -> 20260703_0026" in result.stderr
    assert "20260703_0026 -> 20260723_0027" in result.stderr
    assert "ALTER TABLE incidents ADD COLUMN resolved_by" in result.stdout
    assert "ALTER TABLE incidents ADD COLUMN closed_by" in result.stdout
    assert (
        "CREATE UNIQUE INDEX uq_incidents_tenant_resolution_idempotency"
    ) in result.stdout
    assert (
        "CREATE UNIQUE INDEX uq_incidents_tenant_closure_idempotency"
    ) in result.stdout
    assert "ALTER TABLE workflow_runs ADD COLUMN canceled_by" in result.stdout
    assert (
        "CREATE UNIQUE INDEX uq_workflow_runs_tenant_cancellation_idempotency"
    ) in result.stdout
    assert "CREATE TABLE rca_feedback" in result.stdout
    assert "CREATE UNIQUE INDEX uq_rca_feedback_tenant_idempotency" in result.stdout
    assert "20260723_0028 -> 20260817_0029" in result.stderr
    assert "CREATE TABLE change_events" in result.stdout
    assert "CONSTRAINT uq_change_events_tenant_source_external_event" in result.stdout
    assert "CREATE INDEX ix_change_events_tenant_service_started" in result.stdout
    assert "20260817_0029 -> 20260817_0030" in result.stderr
    assert "ALTER TABLE evidence DROP CONSTRAINT" in result.stdout
    assert "'CHANGE'" in result.stdout
    assert "CREATE TABLE topology_nodes" in result.stdout
    assert "CREATE TABLE knowledge_documents" in result.stdout
    assert "CREATE TABLE workspaces" in result.stdout


def test_postgresql_change_event_downgrade_compiles_offline() -> None:
    """Change Event 迁移必须提供可生成的显式回滚 DDL。"""
    environment = os.environ.copy()
    environment["DEVOPS_AGENT_DATABASE_URL"] = (
        "postgresql+psycopg://migration:secret@localhost/devops_agent"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "downgrade",
            "20260817_0029:20260723_0028",
            "--sql",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "DROP TABLE change_events" in result.stdout
