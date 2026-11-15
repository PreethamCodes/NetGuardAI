"""Small, dependency-free helpers for validating parsed YAML mappings.

Every helper raises :class:`netguard.exceptions.ConfigError` with a dotted
context path (e.g. ``scenario.components[1].rate_mbps``) so that a bad value
in a YAML file can be located quickly.
"""

from __future__ import annotations

import enum
import math
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple, Type, TypeVar

from netguard.exceptions import ConfigError

E = TypeVar("E", bound=enum.Enum)


class _Required:
    """Sentinel meaning "no default: the key must be present"."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "REQUIRED"


REQUIRED: Any = _Required()


def join_ctx(ctx: str, key: str) -> str:
    """Join a context path and a key."""
    return f"{ctx}.{key}" if ctx else key


def require_mapping(value: Any, ctx: str) -> Mapping[str, Any]:
    """Return ``value`` if it is a mapping, else raise ``ConfigError``."""
    if not isinstance(value, Mapping):
        raise ConfigError(f"{ctx or 'document'}: expected a mapping, got {type(value).__name__}")
    return value


def reject_unknown(data: Mapping[str, Any], allowed: Iterable[str], ctx: str) -> None:
    """Raise if ``data`` contains keys outside ``allowed`` (catches typos)."""
    allowed_set = set(allowed)
    unknown = sorted(str(k) for k in data if k not in allowed_set)
    if unknown:
        raise ConfigError(
            f"{ctx or 'document'}: unknown key(s) {unknown}; allowed keys are {sorted(allowed_set)}"
        )


def _lookup(data: Mapping[str, Any], key: str, ctx: str, default: Any) -> Any:
    if key in data and data[key] is not None:
        return data[key]
    if default is REQUIRED:
        raise ConfigError(f"{join_ctx(ctx, key)}: required value is missing")
    return default


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_number(
    value: Any,
    name: str,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
    exclusive_min: bool = False,
) -> float:
    """Validate a numeric scalar and return it as ``float``."""
    if not _is_number(value):
        raise ConfigError(f"{name}: expected a finite number, got {value!r}")
    number = float(value)
    if minimum is not None:
        if exclusive_min and number <= minimum:
            raise ConfigError(f"{name}: must be > {minimum}, got {number}")
        if not exclusive_min and number < minimum:
            raise ConfigError(f"{name}: must be >= {minimum}, got {number}")
    if maximum is not None and number > maximum:
        raise ConfigError(f"{name}: must be <= {maximum}, got {number}")
    return number


def check_int(
    value: Any, name: str, *, minimum: Optional[int] = None, maximum: Optional[int] = None
) -> int:
    """Validate an integer scalar (floats with integral values are rejected)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name}: expected an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name}: must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name}: must be <= {maximum}, got {value}")
    return value


def get_float(
    data: Mapping[str, Any],
    key: str,
    ctx: str,
    default: Any = REQUIRED,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
    exclusive_min: bool = False,
) -> Any:
    """Read an optional/required float. ``None`` defaults are passed through."""
    value = _lookup(data, key, ctx, default)
    if value is None:
        return None
    return check_number(
        value, join_ctx(ctx, key), minimum=minimum, maximum=maximum, exclusive_min=exclusive_min
    )


def get_int(
    data: Mapping[str, Any],
    key: str,
    ctx: str,
    default: Any = REQUIRED,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> Any:
    """Read an optional/required integer."""
    value = _lookup(data, key, ctx, default)
    if value is None:
        return None
    return check_int(value, join_ctx(ctx, key), minimum=minimum, maximum=maximum)


def get_bool(data: Mapping[str, Any], key: str, ctx: str, default: Any = REQUIRED) -> bool:
    """Read an optional/required boolean."""
    value = _lookup(data, key, ctx, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{join_ctx(ctx, key)}: expected true/false, got {value!r}")
    return value


def get_str(
    data: Mapping[str, Any],
    key: str,
    ctx: str,
    default: Any = REQUIRED,
    *,
    choices: Optional[Sequence[str]] = None,
) -> Any:
    """Read an optional/required non-empty string."""
    value = _lookup(data, key, ctx, default)
    if value is None:
        return None
    if key not in data or data[key] is None:
        return value  # the caller's default is trusted as-is
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{join_ctx(ctx, key)}: expected a non-empty string, got {value!r}")
    value = value.strip()
    if choices is not None and value not in choices:
        raise ConfigError(f"{join_ctx(ctx, key)}: must be one of {list(choices)}, got {value!r}")
    return value


def check_range(
    value: Any, name: str, *, minimum: float = 0.0, exclusive_min: bool = False
) -> Tuple[float, float]:
    """Validate a ``[low, high]`` pair with ``low <= high``."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ConfigError(f"{name}: expected a two-element list [low, high], got {value!r}")
    low = check_number(value[0], f"{name}[0]", minimum=minimum, exclusive_min=exclusive_min)
    high = check_number(value[1], f"{name}[1]", minimum=minimum, exclusive_min=exclusive_min)
    if low > high:
        raise ConfigError(f"{name}: low ({low}) must be <= high ({high})")
    return low, high


def parse_enum(enum_cls: Type[E], value: Any, name: str) -> E:
    """Case-insensitive lookup of an enum member by value."""
    if isinstance(value, enum_cls):
        return value
    text = str(value).strip().lower()
    for member in enum_cls:
        if str(member.value).lower() == text:
            return member
    valid = [str(m.value) for m in enum_cls]
    raise ConfigError(f"{name}: invalid value {value!r}; expected one of {valid}")


def as_list(value: Any, name: str) -> List[Any]:
    """Wrap a scalar in a list; pass lists through; reject everything else."""
    if isinstance(value, (list, tuple)):
        if not value:
            raise ConfigError(f"{name}: list must not be empty")
        return list(value)
    if value is None or isinstance(value, Mapping):
        raise ConfigError(f"{name}: expected a string or a list of strings, got {value!r}")
    return [value]
