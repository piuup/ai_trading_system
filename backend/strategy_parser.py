from __future__ import annotations

from typing import Any, Dict

import yaml


class StrategyConfigError(ValueError):
    """Raised when the strategy YAML is invalid."""


REQUIRED_TOP_LEVEL_KEYS = ["name", "description", "entry", "risk_management", "exit", "portfolio"]


def parse_strategy_yaml(config_yaml: str) -> Dict[str, Any]:
    """Validate and normalize the YAML strategy configuration."""
    try:
        data = yaml.safe_load(config_yaml)
    except yaml.YAMLError as exc:
        raise StrategyConfigError(f"Failed to parse YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise StrategyConfigError("Strategy YAML must define a mapping at the top level.")

    missing = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in data]
    if missing:
        raise StrategyConfigError(f"Missing required sections: {', '.join(missing)}")

    data.setdefault("add_position", [])
    data.setdefault("reduce_position", [])
    data.setdefault("take_profit", [])
    data.setdefault("notes", [])

    return data
