"""Load and validate the YAML configuration files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from netguard.config.environment import Environment, LinksConfig, TopologyConfig, TrafficDefaults
from netguard.exceptions import ConfigError

logger = logging.getLogger(__name__)

#: ``<repo>/configs`` - resolved relative to this file so it works from any cwd.
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"

PathLike = Union[str, Path]


def load_yaml(path: PathLike) -> Dict[str, Any]:
    """Read a YAML file that must contain a mapping at its top level."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment problem
        raise ConfigError("PyYAML is not installed (pip install -r requirements.txt)") from exc

    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(data).__name__}")
    return data


def load_environment(config_dir: Optional[PathLike] = None) -> Environment:
    """Load ``topology.yaml``, ``links.yaml`` and ``traffic.yaml`` from ``config_dir``."""
    base = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    try:
        topology = TopologyConfig.from_mapping(load_yaml(base / "topology.yaml"))
        links = LinksConfig.from_mapping(load_yaml(base / "links.yaml"))
        traffic = TrafficDefaults.from_mapping(load_yaml(base / "traffic.yaml"))
    except ConfigError as exc:
        raise ConfigError(f"[{base}] {exc}") from exc
    logger.debug("Loaded environment configuration from %s", base)
    return Environment(topology=topology, links=links, traffic=traffic)
