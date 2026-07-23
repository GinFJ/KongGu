"""Ground-truth metrics for parser/profile/OCR release gates."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def slot_key(value: dict[str, Any]) -> tuple[int, str, int]:
    return (int(value["week"]), str(value["weekday"]), int(value["period"]))


def evaluate_samples(
    truth_samples: Iterable[dict[str, Any]],
    prediction_samples: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    truth_rows = list(truth_samples)
    predictions = {str(row["sample_id"]): row for row in prediction_samples}
    pending = [str(row["sample_id"]) for row in truth_rows if row.get("annotation_status") != "verified"]
    if pending:
        return {
            "ready": False,
            "passed": False,
            "pending_samples": pending,
            "message": "真值尚未完成人工核验，禁止生成准确率结论。",
        }

    truth_slots: set[tuple[str, int, str, int]] = set()
    predicted_slots: set[tuple[str, int, str, int]] = set()
    profile_total = 0
    profile_correct = 0
    per_profile_truth: dict[str, set[tuple[str, int, str, int]]] = defaultdict(set)
    per_profile_pred: dict[str, set[tuple[str, int, str, int]]] = defaultdict(set)

    for truth in truth_rows:
        sample_id = str(truth["sample_id"])
        prediction = predictions.get(sample_id, {})
        profile_total += 1
        if prediction.get("profile") == truth.get("profile"):
            profile_correct += 1
        profile = str(truth.get("profile") or "unknown")
        for slot in truth.get("occupied_slots", []):
            key = (sample_id, *slot_key(slot))
            truth_slots.add(key)
            per_profile_truth[profile].add(key)
        for slot in prediction.get("occupied_slots", []):
            key = (sample_id, *slot_key(slot))
            predicted_slots.add(key)
            per_profile_pred[profile].add(key)

    precision, recall, f1 = _metrics(truth_slots, predicted_slots)
    per_profile = {}
    for profile in sorted(set(per_profile_truth) | set(per_profile_pred)):
        p, r, score = _metrics(per_profile_truth[profile], per_profile_pred[profile])
        per_profile[profile] = {"precision": p, "recall": r, "f1": score}
    profile_accuracy = profile_correct / max(1, profile_total)
    known_bad_false_accepts = sum(
        1
        for truth in truth_rows
        if truth.get("expected_quality_state") == "blocked"
        and predictions.get(str(truth["sample_id"]), {}).get("quality_state") != "blocked"
    )
    passed = (
        profile_accuracy == 1.0
        and f1 >= 0.98
        and all(item["f1"] >= 0.95 for item in per_profile.values())
        and known_bad_false_accepts == 0
    )
    return {
        "ready": True,
        "passed": passed,
        "profile_accuracy": profile_accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "per_profile": per_profile,
        "known_bad_false_accepts": known_bad_false_accepts,
    }


def _metrics(truth: set[Any], predicted: set[Any]) -> tuple[float, float, float]:
    true_positive = len(truth & predicted)
    precision = true_positive / len(predicted) if predicted else (1.0 if not truth else 0.0)
    recall = true_positive / len(truth) if truth else (1.0 if not predicted else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1
