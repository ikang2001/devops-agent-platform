"""Scan public/runtime evaluation inputs for Benchmark-specific answer leakage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from devops_agent_platform.evaluation.integrity import BenchmarkLeakageGuard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source", default="benchmark-input")
    args = parser.parse_args(argv)
    document = json.loads(args.input.read_text(encoding="utf-8"))
    violations = BenchmarkLeakageGuard().scan(document, source=args.source)
    payload = {
        "schema_version": "1.0",
        "source": args.source,
        "violation_count": len(violations),
        "violations": [item.to_dict() for item in violations],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
