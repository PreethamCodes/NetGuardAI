"""Shared fixtures and builders for the test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from netguard.config import DEFAULT_CONFIG_DIR, Environment, load_environment


@pytest.fixture(scope="session")
def config_dir() -> Path:
    return DEFAULT_CONFIG_DIR


@pytest.fixture(scope="session")
def env(config_dir: Path) -> Environment:
    return load_environment(config_dir)


@pytest.fixture
def write_yaml(tmp_path: Path):
    """Write ``data`` (a mapping, or a raw string) into tmp_path and return the path."""

    def _write(name: str, data: Any) -> Path:
        path = tmp_path / name
        path.write_text(data if isinstance(data, str) else yaml.safe_dump(data), encoding="utf-8")
        return path

    return _write


def minimal_component(**overrides: Any) -> Dict[str, Any]:
    """A valid NORMAL iperf3-TCP component; tests tweak single fields."""
    base: Dict[str, Any] = {
        "name": "c1", "pattern": "NORMAL", "tool": "iperf3", "protocol": "tcp",
        "src": "left", "dst": "right", "rate_mbps": 1.0, "clients": 1,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def minimal_scenario(*components: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "name": "t", "duration_s": 120, "seed": 1,
        "components": list(components) or [minimal_component()],
    }
    data.update(overrides)
    return data
