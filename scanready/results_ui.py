"""Read-only presentation of saved evaluation reports."""
from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path

import pandas as pd
import streamlit as st

from .config import load_config
from .metrics import OUTCOMES, REQUIRED_DEFECTS


_STATUS_REPORTS = (
    "metrics.json",
    "per_image.csv",
    "confusion_matrix.png",
    "summary.md",
    "development-disclosure.md",
)
_DEVELOPMENT_LABEL = "Evaluated with disclosed shortfalls"
_PLATFORM_GATES = {
    "fresh_windows_offline_extraction": "unperformed",
    "native_macos": "unperformed",
    "manual_candidate_walkthrough": "unperformed",
}


def _load_development_status(results: Path, payload: dict) -> dict | None:
    path = results / "development_status.json"
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        if status["schema_version"] != 1 or status["population_id"] != "development-approved-v1":
            raise ValueError("Unsupported development status")
        if status["candidate_label"] != _DEVELOPMENT_LABEL or status["qualification"] is not False:
            raise ValueError("Invalid qualification status")
        working = status["working_population"]
        evaluated = status["evaluated_population"]
        omitted = status["omitted_captures"]
        expected_counts = (
            working == {"total": 202, "calibration": 144, "heldout": 58}
            and evaluated == {
                "total": 184,
                "calibration": 137,
                "heldout": 47,
                "scholarly": 184,
                "captured": 0,
            }
            and omitted == {"total": 18, "calibration": 7, "heldout": 11}
        )
        if not expected_counts or evaluated["heldout"] != payload["metrics"]["image_count"]:
            raise ValueError("Population mismatch")
        if status["failed_checks"] != payload["acceptance"]["failures"]:
            raise ValueError("Acceptance mismatch")
        if status["platform_gates"] != _PLATFORM_GATES:
            raise ValueError("Platform-gate mismatch")
        hashes = status["report_sha256"]
        if set(hashes) != set(_STATUS_REPORTS):
            raise ValueError("Report set mismatch")
        for name in _STATUS_REPORTS:
            artifact = results / name
            if (
                not artifact.is_file()
                or not isinstance(hashes[name], str)
                or hashlib.sha256(artifact.read_bytes()).hexdigest() != hashes[name]
            ):
                raise ValueError("Report hash mismatch")
        return status
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def show_evaluation(root: Path) -> None:
    results = root / "results"
    path = results / "metrics.json"
    if not path.is_file():
        st.info("No held-out evaluation results are available yet. Dataset collection and qualification are pending.")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["schema_version"] != 1:
            raise ValueError("Unsupported report version")
        metrics, acceptance = payload["metrics"], payload["acceptance"]
        count = metrics["image_count"]
        accuracy = metrics["recommendation_accuracy"]
        slowest = metrics["timing"]["slowest_seconds"]
        deskew = metrics["deskew"]["accuracy"]
        if type(count) is not int or not 1 <= count <= 2**63 - 1:
            raise ValueError("Report has no evaluated images")
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in (accuracy, slowest, deskew)):
            raise ValueError("Invalid metrics")
        if not 0 <= accuracy <= 1 or not 0 <= deskew <= 1 or slowest < 0:
            raise ValueError("Invalid metric range")
        if type(acceptance["passed"]) is not bool or not isinstance(acceptance["failures"], list):
            raise ValueError("Invalid acceptance result")
        if any(not isinstance(failure, str) for failure in acceptance["failures"]):
            raise ValueError("Invalid acceptance failures")
        if acceptance["passed"] and acceptance["failures"]:
            raise ValueError("Contradictory acceptance result")
        per_defect = metrics["per_defect"]
        if not isinstance(per_defect, dict) or not set(REQUIRED_DEFECTS).issubset(per_defect):
            raise ValueError("Missing per-defect results")
        rates = {"precision", "recall", "f1"}
        counts = {"support", "true_positive", "false_positive", "false_negative"}
        for name, values in per_defect.items():
            if not isinstance(name, str) or not isinstance(values, dict) or set(values) != rates | counts:
                raise ValueError("Invalid per-defect schema")
            for key in rates:
                value = values[key]
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("Invalid per-defect rate")
            for key in counts:
                if type(values[key]) is not int or not 0 <= values[key] <= count:
                    raise ValueError("Invalid per-defect count")
        matrix = metrics["confusion_matrix"]
        if metrics["outcome_order"] != list(OUTCOMES) or not isinstance(matrix, list) or len(matrix) != 3:
            raise ValueError("Invalid confusion matrix")
        for row in matrix:
            if not isinstance(row, list) or len(row) != 3 or any(type(v) is not int or not 0 <= v <= count for v in row):
                raise ValueError("Invalid confusion matrix counts")
        if sum(sum(row) for row in matrix) != count:
            raise ValueError("Confusion matrix does not match image count")
        defects = pd.DataFrame.from_dict(per_defect, orient="index")
        for column in ("precision", "recall", "f1"):
            defects[column] = [
                "—" if not values["support"] else f"{values[column]:.3f}"
                for values in (per_defect[name] for name in defects.index)
            ]
        confusion = pd.DataFrame(matrix, index=OUTCOMES, columns=OUTCOMES)
        config_hash = payload["config_sha256"]
        current_hash = load_config(root / "config/thresholds.yaml").sha256
        seed = payload["seed"]
        versions = payload["package_versions"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        st.error("Evaluation results could not be loaded. Restore the complete reports or rerun held-out evaluation with the frozen configuration.")
        return

    st.subheader("Held-out evaluation")
    columns = st.columns(4)
    columns[0].metric("Held-out images", count)
    columns[1].metric("Recommendation accuracy", f"{accuracy:.1%}")
    columns[2].metric("Slowest analysis", f"{slowest:.2f} s")
    if metrics["deskew"]["eligible"]:
        columns[3].metric("Deskew success", f"{deskew:.1%}")
    else:
        columns[3].metric("Deskew success", "N/A", help="No eligible 2–15° held-out rotations were evaluated.")
    failures = acceptance["failures"]
    if acceptance["passed"]:
        st.caption("All evaluation acceptance requirements were met.")
    else:
        st.caption(f"Evaluation acceptance: {len(failures)} requirement{'s' if len(failures) != 1 else ''} not met (listed below).")
    status = _load_development_status(results, payload)
    if status is None:
        st.warning(
            "Development status metadata is missing, invalid or stale. "
            "These reports do not establish candidate status."
        )
    else:
        evaluated = status["evaluated_population"]
        st.info(
            f"{_DEVELOPMENT_LABEL}: these results come from {evaluated['heldout']} held-out scans that were never "
            f"used for tuning ({evaluated['total']} eligible scans in total). The original acceptance bar was not "
            "met; the shortfalls are listed below and are accepted as the project's documented cut line."
        )
        if evaluated["captured"] == 0:
            st.warning("This evaluation contains zero captured rows; capture performance is unavailable.")
    if metrics["deskew"]["eligible"] == 0:
        st.warning("There are no eligible deskew cases; deskew qualification is unavailable.")
    if config_hash != current_hash:
        st.warning("These results use a different configuration. They do not qualify the current settings.")
    elif acceptance["passed"]:
        st.success("Evaluation acceptance: PASS. Archive and platform qualification are separate checks.")
    elif status is not None:
        st.warning("Original acceptance bar: not met — accepted as a documented cut line. Unmet requirements:")
    else:
        st.error("Evaluation acceptance: FAIL")
    for failure in failures:
        st.markdown(f"- {failure}")
    st.subheader("Per-defect performance")
    st.caption("— means there were no examples of that defect, so precision, recall and F1 can't be measured; false positives still count.")
    st.dataframe(defects, use_container_width=True)
    st.subheader("Outcome confusion matrix")
    st.caption("Rows are expected outcomes; columns are predicted outcomes.")
    st.dataframe(confusion, use_container_width=True)
    st.caption(f"Seed: {seed} · Configuration SHA-256: {config_hash}")
    with st.expander("Environment used for evaluation"):
        st.table(pd.DataFrame(sorted(versions.items()), columns=["Package", "Version"]))
    for name, mime in (("metrics.json", "application/json"), ("per_image.csv", "text/csv"), ("summary.md", "text/markdown")):
        artifact = results / name
        if artifact.is_file():
            st.download_button(f"Download {name}", artifact.read_bytes(), file_name=name, mime=mime)
