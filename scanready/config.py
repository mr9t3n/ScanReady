from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "thresholds.yaml"


@dataclass(frozen=True)
class Config:
    values: dict[str, Any]
    sha256: str

    def value(self, section: str, key: str) -> Any:
        return self.values[section][key]


def load_config(path: str | Path = DEFAULT_CONFIG) -> Config:
    source = Path(path)
    raw = source.read_bytes()
    values = yaml.safe_load(raw)
    if not isinstance(values, dict):
        raise ValueError("ScanReady configuration must be a mapping.")
    return Config(values=values, sha256=sha256(raw).hexdigest())
