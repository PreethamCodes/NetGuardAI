"""Exception hierarchy for NetGuard AI."""

from __future__ import annotations


class NetGuardError(Exception):
    """Base class for all NetGuard AI errors."""


class ConfigError(NetGuardError):
    """A YAML configuration file is missing, malformed or has invalid values."""


class TopologyError(NetGuardError):
    """The Mininet topology could not be built, shaped or verified."""


class TrafficError(NetGuardError):
    """A traffic event is invalid or a traffic generator failed to run."""
