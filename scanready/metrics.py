from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


OUTCOMES = ("READY", "READY AFTER CORRECTION", "RETRY CAPTURE")
REQUIRED_DEFECTS = ("blur", "skew", "glare", "shadow", "low_contrast", "cropped_edges")

_DEFECT_ALIASES = {
    "rotation": "skew",
    "contrast": "low_contrast",
    "contrast_loss": "low_contrast",
    "crop": "cropped_edges",
    "cropped": "cropped_edges",
}


def canonical_defect(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
    return _DEFECT_ALIASES.get(normalized, normalized)


def _defects(record: Mapping[str, Any], key: str) -> set[str]:
    raw = record.get(key, set())
    if isinstance(raw, str):
        values = {value for value in raw.replace(",", ";").split(";") if value.strip()}
    else:
        values = {str(value) for value in raw}
    return {canonical_defect(value) for value in values if canonical_defect(value) not in {"", "clean", "none"}}


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def evaluation_metrics(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    outcome_index = {outcome: index for index, outcome in enumerate(OUTCOMES)}
    confusion = [[0 for _ in OUTCOMES] for _ in OUTCOMES]
    correct = 0
    for row in rows:
        expected = str(row["expected_outcome"])
        predicted = str(row["predicted_outcome"])
        if expected not in outcome_index or predicted not in outcome_index:
            raise ValueError(f"Unknown outcome pair: expected={expected!r}, predicted={predicted!r}")
        confusion[outcome_index[expected]][outcome_index[predicted]] += 1
        correct += expected == predicted

    all_defects = set(REQUIRED_DEFECTS)
    for row in rows:
        all_defects.update(_defects(row, "expected_defects"))
        all_defects.update(_defects(row, "predicted_defects"))
    per_defect: dict[str, dict[str, float | int]] = {}
    for defect in sorted(all_defects):
        true_positive = false_positive = false_negative = support = 0
        for row in rows:
            expected = defect in _defects(row, "expected_defects")
            predicted = defect in _defects(row, "predicted_defects")
            support += expected
            true_positive += expected and predicted
            false_positive += not expected and predicted
            false_negative += expected and not predicted
        precision = _ratio(true_positive, true_positive + false_positive)
        recall = _ratio(true_positive, true_positive + false_negative)
        f1 = round(2 * precision * recall / (precision + recall), 6) if precision + recall else 0.0
        per_defect[defect] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
        }

    eligible = successful = 0
    for row in rows:
        try:
            true_skew = abs(float(row.get("true_skew_deg", 0.0)))
        except (TypeError, ValueError):
            true_skew = 0.0
        residual = row.get("residual_skew_deg")
        if (row.get("expected_outcome") == "READY AFTER CORRECTION"
                and "skew" in _defects(row, "expected_defects")
                and 2.0 <= true_skew <= 15.0):
            eligible += 1
            successful += residual is not None and abs(float(residual)) <= 1.5

    elapsed = [float(row.get("elapsed_seconds", 0.0)) for row in rows]
    return {
        "outcome_order": list(OUTCOMES),
        "image_count": len(rows),
        "recommendation_accuracy": _ratio(correct, len(rows)),
        "confusion_matrix": confusion,
        "per_defect": per_defect,
        "deskew": {"eligible": eligible, "successful": successful, "accuracy": _ratio(successful, eligible)},
        "timing": {
            "slowest_seconds": round(max(elapsed), 6) if elapsed else 0.0,
            "mean_seconds": round(sum(elapsed) / len(elapsed), 6) if elapsed else 0.0,
        },
    }


def acceptance_checks(records: Iterable[Mapping[str, Any]], metrics: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(records)
    failures: list[str] = []
    if len(rows) < 50:
        failures.append(f"Held-out evaluation requires at least 50 held-out images; found {len(rows)}.")
    controls = sum(not _defects(row, "expected_defects") for row in rows)
    if controls < 10:
        failures.append(f"Held-out evaluation requires at least 10 clean controls; found {controls}.")
    for defect in REQUIRED_DEFECTS:
        count = sum(defect in _defects(row, "expected_defects") for row in rows)
        if count < 6:
            failures.append(f"Held-out evaluation requires at least 6 {defect} examples; found {count}.")
    accuracy = float(metrics["recommendation_accuracy"])
    if accuracy < 0.80:
        failures.append(f"Recommendation accuracy must be at least 0.80; measured {accuracy:.3f}.")
    slowest = float(metrics["timing"]["slowest_seconds"])
    if slowest > 10.0:
        failures.append(f"The slowest analysis must be at most 10 seconds; measured {slowest:.3f} seconds.")
    errors = [row for row in rows if str(row.get("error", "")).strip()]
    if errors:
        failures.append(f"Held-out evaluation contains {len(errors)} unhandled errors.")
    deskew = metrics["deskew"]
    if int(deskew["eligible"]) == 0:
        failures.append("Deskew accuracy requires recoverable 2-15 degree held-out rotations; found 0.")
    elif float(deskew["accuracy"]) < 0.80:
        failures.append(f"Deskew accuracy must be at least 0.80; measured {float(deskew['accuracy']):.3f}.")
    return {"passed": not failures, "failures": failures}

