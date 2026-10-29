from __future__ import annotations

from collections.abc import Iterable

from .report import CheckResult, Outcome, Status


def decide(checks: Iterable[CheckResult]) -> Outcome:
    results = tuple(checks)
    if any(item.status is Status.FAIL and not item.recoverable for item in results):
        return Outcome.RETRY_CAPTURE
    if any(item.status is Status.FAIL and item.recoverable for item in results):
        return Outcome.READY_AFTER_CORRECTION
    return Outcome.READY
