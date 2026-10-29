from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class Outcome(str, Enum):
    READY = "READY"
    READY_AFTER_CORRECTION = "READY AFTER CORRECTION"
    RETRY_CAPTURE = "RETRY CAPTURE"


@dataclass(frozen=True)
class Region:
    kind: str
    points: tuple[tuple[int, int], ...]
    label: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    severity: float
    recoverable: bool
    correction: str | None
    measurement: float
    unit: str
    limit: float
    message: str
    next_action: str
    regions: tuple[Region, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedPage:
    original_bytes: bytes
    source_kind: str
    display_name: str
    image_bgr: np.ndarray


@dataclass(frozen=True)
class AnalysisReport:
    outcome: Outcome
    checks: tuple[CheckResult, ...]
    reasons: tuple[str, ...]
    image_size: tuple[int, int]
    elapsed_seconds: float
    config_sha256: str
