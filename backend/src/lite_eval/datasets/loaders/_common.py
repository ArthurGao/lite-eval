"""What every benchmark reader shares."""

from dataclasses import dataclass, field
from typing import Any

from lite_eval.contracts import Case
from lite_eval.errors import ConfigError


@dataclass(frozen=True)
class Source:
    name: str  # the benchmark's name
    url: str
    license: str


@dataclass(frozen=True)
class Loaded:
    cases: list[Case]
    notes: list[str] = field(default_factory=list)  # what was left out, for the description
    corpus: dict[str, Any] = field(default_factory=dict)  # corpus/<name>.json -> its content


def field_of(item: Any, key: str, where: str) -> Any:
    """item[key], or a ConfigError naming the item and the key."""
    if not isinstance(item, dict) or key not in item:
        raise ConfigError(f"{where}: missing {key!r}")
    return item[key]


def text(value: Any) -> str | None:
    """Answers may be numbers in the official files; cases keep them as text."""
    return None if value is None else str(value)
