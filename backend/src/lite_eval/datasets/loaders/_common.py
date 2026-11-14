"""What every benchmark reader shares."""

import re
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
class Item:
    """One case, and the corpus files it is the first to need (corpus/<name>.json -> content)."""

    case: Case
    corpus: dict[str, Any] = field(default_factory=dict)


# Names a corpus file: no separators, no leading dot.
CORPUS_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def corpus_name(name: str, what: str) -> str:
    if not CORPUS_NAME.match(name):
        raise ConfigError(f"{what} {name!r} cannot name a corpus file")
    return name


def field_of(item: Any, key: str, where: str) -> Any:
    """item[key], or a ConfigError naming the item and the key."""
    if not isinstance(item, dict) or key not in item:
        raise ConfigError(f"{where}: missing {key!r}")
    return item[key]


def text(value: Any) -> str | None:
    """Answers may be numbers in the official files; cases keep them as text."""
    return None if value is None else str(value)
