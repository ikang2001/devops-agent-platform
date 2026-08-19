from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_SOURCE_TO_TOOL = {
    "prometheus": "metrics.query@v1",
    "loki": "logs.query@v1",
    "tempo": "traces.query@v1",
    "change": "changes.query@v1",
    "topology": "topology.query@v1",
    "knowledge": "knowledge.search@v1",
    "http": "http.observe@v1",
}


def _entry_service(document: dict[str, Any]) -> str | None:
    trigger = document.get("trigger")
    if not isinstance(trigger, dict):
        return None
    path = trigger.get("path")
    if not isinstance(path, str):
        return None
    segment = path.strip("/").split("/", 1)[0].strip().casefold()
    if not segment or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
        for character in segment
    ):
        return None
    return segment if segment.endswith("-service") else f"{segment}-service"


def build_reference_suite(
    scenario_directory: Path,
    *,
    simulation: bool = False,
) -> dict[str, Any]:
    """从公开信号描述构造合成 Runtime 快照，不复制 Ground Truth。"""
    cases = []
    versions: set[str] = set()
    for path in sorted(scenario_directory.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        versions.add(str(document["schema_version"]))
        alert = document.get("alert_mapping", {})
        signals = document.get("expected_signals", [])
        evidence = [
            {
                "evidence_id": signal["evidence_id"],
                "evidence_type": signal["evidence_type"],
                "source": signal["source"],
                "summary": signal["assertion"],
            }
            for signal in signals
        ]
        tool_calls = [
            {
                "tool_type": _SOURCE_TO_TOOL.get(signal["source"], "reference.read@v1"),
                "status": "SUCCEEDED",
                "evidence_ids": [signal["evidence_id"]],
            }
            for signal in signals
        ]
        tool_calls.append(
            {
                "tool_type": "runbooks.retrieve@v1",
                "status": "SUCCEEDED",
                "evidence_ids": [],
            }
        )
        cases.append(
            {
                "scenario_id": document["scenario_id"],
                "incident_id": f"reference-{document['scenario_id']}",
                "service_name": alert.get("service_name", document["service_name"]),
                "entry_service": _entry_service(document),
                "summary": alert.get("summary", "Synthetic reference incident"),
                "evidence": evidence,
                "tool_calls": tool_calls,
                "investigation_steps": len(tool_calls),
            }
        )
    if not cases:
        raise ValueError("no scenario manifests found")
    if len(versions) != 1:
        raise ValueError("scenario manifests do not share one schema version")
    return {
        "schema_version": "1.0",
        "suite": "minishop-v2-reference-runtime",
        "scenario_version": versions.pop(),
        "synthetic": not simulation,
        "simulation": simulation,
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build synthetic live benchmark input")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--simulation",
        action="store_true",
        help="mark the suite as a production-like local simulation",
    )
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("reference input already exists; choose a new output path")
    document = build_reference_suite(args.scenarios, simulation=args.simulation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
