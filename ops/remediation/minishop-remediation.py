"""MiniShop 演练修复计划、审批、执行和回滚 CLI。"""

import argparse
import asyncio
import json
from pathlib import Path

from devops_agent_platform.infrastructure.remediation import (
    MiniShopRemediationConfig,
    MiniShopRemediationService,
    SQLiteRemediationApprovalStore,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Operate the allowlisted MiniShop remediation approval loop. "
            "Execution is disabled unless --enable-execution is present."
        )
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(".tmp/minishop-remediation.sqlite3"),
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path("MiniShop 电商下单故障演练靶场/scenarios"),
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:18080",
    )
    parser.add_argument("--allow-insecure-http", action="store_true")
    parser.add_argument("--enable-execution", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("scenario_id")
    plan.add_argument("--created-by", required=True)
    plan.add_argument("--trace-id", required=True)
    plan.add_argument("--ttl-seconds", type=int, default=900)

    approve = subparsers.add_parser("approve")
    _add_transition_arguments(approve, actor_name="approved-by")

    execute = subparsers.add_parser("execute")
    _add_transition_arguments(execute, actor_name="actor")

    rollback = subparsers.add_parser("rollback")
    _add_transition_arguments(rollback, actor_name="actor")

    show = subparsers.add_parser("show")
    show.add_argument("plan_id")

    audit = subparsers.add_parser("audit")
    audit.add_argument("plan_id")
    return parser


def _add_transition_arguments(
    parser: argparse.ArgumentParser,
    *,
    actor_name: str,
) -> None:
    parser.add_argument("plan_id")
    parser.add_argument("--expected-version", type=int, required=True)
    parser.add_argument(f"--{actor_name}", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--trace-id", required=True)


async def run(args: argparse.Namespace) -> object:
    config = MiniShopRemediationConfig(
        scenario_directory=args.scenarios,
        target_base_url=args.base_url,
        enabled=args.enable_execution,
        allow_insecure_http=args.allow_insecure_http,
    )
    store = SQLiteRemediationApprovalStore(args.database)
    service = MiniShopRemediationService(config, store)
    try:
        if args.command == "plan":
            return service.create_plan(
                scenario_id=args.scenario_id,
                created_by=args.created_by,
                trace_id=args.trace_id,
                ttl_seconds=args.ttl_seconds,
            ).to_dict()
        if args.command == "approve":
            return service.approve(
                plan_id=args.plan_id,
                expected_version=args.expected_version,
                approved_by=args.approved_by,
                idempotency_key=args.idempotency_key,
                trace_id=args.trace_id,
            ).to_dict()
        if args.command == "execute":
            return (
                await service.execute(
                    plan_id=args.plan_id,
                    expected_version=args.expected_version,
                    actor=args.actor,
                    idempotency_key=args.idempotency_key,
                    trace_id=args.trace_id,
                )
            ).to_dict()
        if args.command == "rollback":
            return (
                await service.rollback(
                    plan_id=args.plan_id,
                    expected_version=args.expected_version,
                    actor=args.actor,
                    idempotency_key=args.idempotency_key,
                    trace_id=args.trace_id,
                )
            ).to_dict()
        if args.command == "show":
            return service.get(args.plan_id).to_dict()
        return [item.to_dict() for item in service.list_audit(args.plan_id)]
    finally:
        await service.close()


def main() -> None:
    args = build_parser().parse_args()
    print(
        json.dumps(
            asyncio.run(run(args)),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
