"""Evaluate anonymized parser predictions against the verified 12-sample truth set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.benchmark import evaluate_samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", default="benchmarks/ground_truth/manifest.json")
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    predictions = json.loads(Path(args.predictions).read_text(encoding="utf-8"))
    metrics = evaluate_samples(truth.get("samples", []), predictions.get("samples", []))
    encoded = json.dumps(metrics, ensure_ascii=False, indent=2)
    print(encoded)
    if args.out:
        Path(args.out).write_text(encoded + "\n", encoding="utf-8")
    return 0 if metrics.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
