"""Pattern-specific options: schema, defaults and validation.

Each traffic pattern accepts a small set of extra options in the ``options``
mapping of a scenario component. Unknown options are rejected so that typos
in YAML files do not silently fall back to defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from netguard.exceptions import ConfigError
from netguard.traffic.types import Pattern, Protocol
from netguard.utils.validation import (
    REQUIRED,
    check_int,
    check_number,
    check_range,
    join_ctx,
    parse_enum,
    reject_unknown,
    require_mapping,
)


@dataclass(frozen=True)
class Opt:
    """Schema of one option. ``kind`` is float | int | range | protocols."""

    kind: str
    default: Any
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    exclusive_min: bool = False


PATTERN_OPTIONS: Dict[Pattern, Dict[str, Opt]] = {
    Pattern.NORMAL: {
        # Each client starts at a random offset in [0, start_jitter_s].
        "start_jitter_s": Opt("float", 5.0, minimum=0.0),
    },
    Pattern.BURST: {
        # Steady background load (per client) kept running between bursts.
        "base_rate_mbps": Opt("float", 0.0, minimum=0.0),
        "burst_duration_s": Opt("float", 10.0, minimum=0.0, exclusive_min=True),
        "burst_count": Opt("int", 1, minimum=1, maximum=1000),
    },
    Pattern.PERIODIC: {
        "period_s": Opt("float", REQUIRED, minimum=0.0, exclusive_min=True),
        "on_duration_s": Opt("float", 5.0, minimum=0.0, exclusive_min=True),
    },
    Pattern.RANDOM: {
        "rate_range_mbps": Opt("range", REQUIRED, minimum=0.0, exclusive_min=True),
        "duration_range_s": Opt("range", REQUIRED, minimum=0.0, exclusive_min=True),
        "gap_range_s": Opt("range", (0.0, 5.0), minimum=0.0),
        "protocols": Opt("protocols", ("tcp", "udp")),
    },
    Pattern.RAMP: {
        # Aggregate offered load as a fraction of the bottleneck capacity.
        "start_load": Opt("float", 0.1, minimum=0.0, exclusive_min=True),
        "onset_load": Opt("float", 1.05, minimum=0.0, exclusive_min=True),
        "max_load": Opt("float", 1.3, minimum=0.0, exclusive_min=True),
        "onset_s": Opt("float", 60.0, minimum=0.0, exclusive_min=True),
        "step_s": Opt("float", 5.0, minimum=1.0),
    },
    Pattern.FLASH_CROWD: {
        # All clients arrive within this window after ``start_s``.
        "spread_s": Opt("float", 2.0, minimum=0.0),
    },
    Pattern.UDP_FLOOD: {},
}


def resolve_options(pattern: Pattern, raw: Optional[Mapping[str, Any]], ctx: str) -> Dict[str, Any]:
    """Validate ``raw`` against the schema of ``pattern`` and fill in defaults."""
    schema = PATTERN_OPTIONS[pattern]
    raw = require_mapping(raw or {}, ctx)
    reject_unknown(raw, schema.keys(), ctx)
    resolved: Dict[str, Any] = {}
    for key, opt in schema.items():
        name = join_ctx(ctx, key)
        value = raw.get(key)
        if value is None:
            if opt.default is REQUIRED:
                raise ConfigError(f"{name}: required for pattern {pattern.value}")
            value = opt.default
        if opt.kind == "float":
            resolved[key] = check_number(
                value, name, minimum=opt.minimum, maximum=opt.maximum, exclusive_min=opt.exclusive_min
            )
        elif opt.kind == "int":
            resolved[key] = check_int(
                value, name, minimum=int(opt.minimum) if opt.minimum is not None else None,
                maximum=int(opt.maximum) if opt.maximum is not None else None,
            )
        elif opt.kind == "range":
            resolved[key] = check_range(
                value, name, minimum=opt.minimum or 0.0, exclusive_min=opt.exclusive_min
            )
        elif opt.kind == "protocols":
            if not isinstance(value, (list, tuple)) or not value:
                raise ConfigError(f"{name}: expected a non-empty list such as [tcp, udp]")
            protos = tuple(parse_enum(Protocol, v, name).value for v in value)
            if not set(protos) <= {Protocol.TCP.value, Protocol.UDP.value}:
                raise ConfigError(f"{name}: only tcp and udp are supported, got {list(protos)}")
            resolved[key] = protos
        else:  # pragma: no cover - schema bug
            raise AssertionError(f"unknown option kind {opt.kind}")
    return resolved
