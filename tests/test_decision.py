from __future__ import annotations

from scanready.decision import decide
from scanready.report import CheckResult, Outcome, Status


def result(*, status: Status, recoverable: bool = False) -> CheckResult:
    return CheckResult(
        name="example",
        status=status,
        severity=0.0,
        recoverable=recoverable,
        correction=None,
        measurement=0.0,
        unit="score",
        limit=1.0,
        message="message",
        next_action="action",
    )


def test_decision_truth_table() -> None:
    assert decide([result(status=Status.PASS)]) is Outcome.READY
    assert decide([result(status=Status.WARN)]) is Outcome.READY
    assert decide([result(status=Status.FAIL, recoverable=True)]) is Outcome.READY_AFTER_CORRECTION
    assert decide([result(status=Status.FAIL, recoverable=False)]) is Outcome.RETRY_CAPTURE
    assert decide(
        [result(status=Status.FAIL, recoverable=True), result(status=Status.FAIL, recoverable=False)]
    ) is Outcome.RETRY_CAPTURE
