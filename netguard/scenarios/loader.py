"""Locate and load scenario YAML files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Union

from netguard.config.loader import DEFAULT_CONFIG_DIR, load_yaml
from netguard.exceptions import ConfigError
from netguard.scenarios.models import ScenarioConfig

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def scenarios_dir(config_dir: Optional[PathLike] = None) -> Path:
    base = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    return base / "scenarios"


def list_scenarios(config_dir: Optional[PathLike] = None) -> List[str]:
    """Names (file stems) of all scenario files available in ``config_dir``."""
    directory = scenarios_dir(config_dir)
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml"))


def load_scenario(name_or_path: PathLike, config_dir: Optional[PathLike] = None) -> ScenarioConfig:
    """Load a scenario by name (``ramp``) or by path to a YAML file."""
    candidate = Path(name_or_path)
    if candidate.suffix in (".yaml", ".yml") or candidate.is_file():
        path = candidate
    else:
        path = scenarios_dir(config_dir) / f"{name_or_path}.yaml"
        if not path.is_file():
            available = list_scenarios(config_dir)
            raise ConfigError(f"scenario {str(name_or_path)!r} not found in {path.parent}; available: {available}")
    try:
        scenario = ScenarioConfig.from_mapping(load_yaml(path), source=path.name)
    except ConfigError as exc:
        raise ConfigError(f"[{path}] {exc}") from exc
    logger.debug("Loaded scenario %s from %s", scenario.name, path)
    return scenario
