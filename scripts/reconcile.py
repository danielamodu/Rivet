#!/usr/bin/env python3
"""Run the deterministic reconciliation engine on a JSON input document."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rivet.reconciliation import InputValidationError, load_input, reconcile  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="path to a schema_version 1 JSON input")
    args = parser.parse_args()
    try:
        result = reconcile(load_input(args.input))
    except (InputValidationError, OSError) as exc:
        print(f"invalid reconciliation input: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
