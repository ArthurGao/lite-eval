"""The JSONPath subset HTTP targets read replies with (design §18.11): `$`, `.key`, `[n]`, `[*]`.

Stricter than RFC 9535 on purpose: a key or index missing under a wildcard is an error, not a
shorter node list, so a reply that lost a field is `invalid_output` rather than fewer hits.
"""

import re
from dataclasses import dataclass
from typing import Any

from lite_eval.errors import ConfigError

_STEP = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\*|-?[0-9]+)\]")
Step = str | int | None  # key, index, wildcard


@dataclass(frozen=True)
class JsonPath:
    text: str
    steps: tuple[Step, ...]

    def select(self, value: Any) -> Any:
        """The selected value; a list under a wildcard. LookupError when anything is missing."""
        return _select(value, self.steps, self.text)


def compile_path(text: str) -> JsonPath:
    if not text.startswith("$"):
        raise ConfigError(f"JSONPath {text!r} must start with '$'")
    steps: list[Step] = []
    pos = 1
    while pos < len(text):
        m = _STEP.match(text, pos)
        if m is None:
            raise ConfigError(
                f"JSONPath {text!r}: unsupported at {text[pos:]!r} (only .key, [n] and [*])"
            )
        key, index = m.groups()
        steps.append(key if key is not None else (None if index == "*" else int(index)))
        pos = m.end()
    return JsonPath(text, tuple(steps))


def _select(value: Any, steps: tuple[Step, ...], text: str) -> Any:
    for i, step in enumerate(steps):
        if step is None:
            if not isinstance(value, list):
                raise LookupError(f"{text}: [*] on a {type(value).__name__}")
            return [_select(item, steps[i + 1 :], text) for item in value]
        if isinstance(step, str):
            if not isinstance(value, dict) or step not in value:
                raise LookupError(f"{text}: no key {step!r}")
            value = value[step]
        else:
            if not isinstance(value, list) or not -len(value) <= step < len(value):
                raise LookupError(f"{text}: no index {step}")
            value = value[step]
    return value
