"""Redaction of strings before they are written or shown (design §18.12).

Built-in rules:
- `connection_strings`: the password of a URL (`scheme://user:PASSWORD@host`), any query
  parameter whose name contains "password", and libpq `key=value` parameters whose name contains
  "password" (quoted with libpq's rules or bare up to the next space; the approach of lite-mem's
  `errors.redact`). Host, user and database stay readable.
- `api_keys`: `sk-…`, `stw_…`, `Bearer …`, `AKIA…`, and the value after `api_key` / `token` /
  `secret` with `=` or `:`; in JSON, the string value of a key named like one of those.
- `emails`.
Suite-defined patterns replace their whole match. Every replacement is `«redacted:<rule>»`;
redacting twice gives the same text.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Protocol

from lite_eval.contracts import Output, RedactConfig, Score
from lite_eval.errors import ConfigError


def _mark(rule: str) -> str:
    return f"«redacted:{rule}»"


_MARK_TAIL = r"(?!«redacted)"  # never re-redact a marker

# Each pattern has a `pre` group (kept) and a `secret` group (replaced by the marker).
_CONNECTION = (
    # scheme://user:PASSWORD@
    re.compile(
        r"(?P<pre>\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]*:)"
        + _MARK_TAIL
        + r"(?P<secret>[^\s@/]+)(?=@)"
    ),
    # ?sslpassword=VALUE&
    re.compile(
        r"(?P<pre>[?&][\w.-]*password=)" + _MARK_TAIL + r"(?P<secret>[^\s&#]+)", re.IGNORECASE
    ),
    # password='a b' / PGPASSWORD=x / db_password = "y"
    re.compile(
        r"(?P<pre>(?<![?&\w.-])\w*password\s*=\s*)"
        + _MARK_TAIL
        + r"(?P<secret>'(?:[^'\\]|\\.)*'|\"[^\"]*\"|[^\s'\"]+)",
        re.IGNORECASE,
    ),
)

_API_KEYS = (
    re.compile(r"(?P<pre>)(?P<secret>\bsk-[A-Za-z0-9_-]{16,})"),
    re.compile(r"(?P<pre>)(?P<secret>\bstw_[A-Za-z0-9_-]{16,})"),
    re.compile(r"(?P<pre>)(?P<secret>\bAKIA[0-9A-Z]{16}\b)"),
    re.compile(r"(?P<pre>\bBearer\s+)(?P<secret>[A-Za-z0-9._~+/=-]{8,})"),
    re.compile(
        r"(?P<pre>\b(?:api[_-]?key|token|secret)\b[\"']?\s*[:=]\s*)"
        + _MARK_TAIL
        + r"(?P<secret>\"[^\"]*\"|'[^']*'|[^\s,;&\"'}\])]+)",
        re.IGNORECASE,
    ),
)

_EMAILS = (re.compile(r"(?P<pre>)(?P<secret>\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b)"),)

_BUILTIN: dict[str, tuple[re.Pattern[str], ...]] = {
    "connection_strings": _CONNECTION,
    "api_keys": _API_KEYS,
    "emails": _EMAILS,
}

_BUILTIN_NAMES = tuple(_BUILTIN)

# A JSON object key whose string value is a secret as a whole (api_keys rule).
_SECRET_KEY = re.compile(r"^(?:.*[_-])?(?:api[_-]?key|token|secret|password)$", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    rule: str
    match: str


@dataclass(frozen=True)
class Redactor:
    rules: tuple[str, ...] = ()
    patterns: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = [r for r in self.rules if r not in _BUILTIN]
        if unknown:
            raise ConfigError(
                f"unknown redaction rule(s) {unknown}; built-in: {list(_BUILTIN_NAMES)}"
            )
        for name in self.patterns:
            if name in _BUILTIN:
                raise ConfigError(f"custom redaction rule {name!r} shadows a built-in rule")
        self._compiled  # noqa: B018 (compile now, so a bad pattern fails at load time)

    @cached_property
    def _compiled(self) -> tuple[tuple[str, re.Pattern[str]], ...]:
        """(marker, pattern) pairs, built once per redactor."""
        out = [(_mark(rule), p) for rule in self.rules for p in _BUILTIN[rule]]
        for name, pattern in self.patterns.items():
            try:
                out.append((_mark(name), re.compile(f"(?P<pre>)(?P<secret>{pattern})")))
            except re.error as exc:
                raise ConfigError(f"redaction rule {name!r}: invalid regex: {exc}") from exc
        return tuple(out)

    @property
    def active(self) -> bool:
        return bool(self._compiled)

    @classmethod
    def from_config(cls, config: RedactConfig) -> "Redactor":
        return cls(rules=tuple(config.rules), patterns=dict(config.patterns))

    def to_config(self) -> RedactConfig:
        return RedactConfig(rules=list(self.rules), patterns=dict(self.patterns))

    def text(self, value: str) -> str:
        for marker, pattern in self._compiled:
            value = pattern.sub(lambda m: m.group("pre") + marker, value)  # noqa: B023 (used at once)
        return value

    def json(self, value: Any) -> Any:
        """Redact every string inside a JSON value; keys are kept."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.json(v) for v in value]
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if "api_keys" in self.rules and isinstance(v, str) and _SECRET_KEY.match(k):
                    out[k] = v if v.startswith("«redacted:") else _mark("api_keys")
                else:
                    out[k] = self.json(v)
            return out
        return value

    def output(self, output: Output) -> Output:
        """Every string an Output carries: value, span input / output / attrs, error message."""
        if not self.active:
            return output
        spans = [
            s.model_copy(
                update={
                    "input": self.json(s.input),
                    "output": self.json(s.output),
                    "attrs": self.json(s.attrs),
                }
            )
            for s in output.trace.spans
        ]
        error = output.error
        if error is not None:
            error = error.model_copy(update={"message": self.text(error.message)})
        return output.model_copy(
            update={
                "value": self.json(output.value),
                "trace": output.trace.model_copy(update={"spans": spans}),
                "error": error,
            }
        )

    def score(self, score: Score) -> Score:
        """Reason, evidence, error message and the judge's raw reply."""
        if not self.active:
            return score
        update: dict[str, Any] = {
            "reason": self.text(score.reason),
            "evidence": self.json(score.evidence),
        }
        if score.error is not None:
            update["error"] = score.error.model_copy(
                update={"message": self.text(score.error.message)}
            )
        if score.judge is not None:
            update["judge"] = score.judge.model_copy(update={"raw": self.json(score.judge.raw)})
        return score.model_copy(update=update)

    def findings(self, value: str) -> list[Finding]:
        """What `text` would replace, for the dataset secret scan."""
        return [
            Finding(marker.removeprefix("«redacted:").removesuffix("»"), m.group("secret"))
            for marker, pattern in self._compiled
            for m in pattern.finditer(value)
        ]


def scan_rules() -> Redactor:
    """The rules a dataset is scanned with when it is loaded (design §18.12)."""
    return Redactor(rules=("connection_strings", "api_keys"))


class _Named(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def pattern(self) -> str: ...


def from_spec(spec: Iterable[str | _Named]) -> Redactor:
    """A Redactor from a suite's `redact:` list: rule names or {name, pattern} rules."""
    items = list(spec)
    rules = tuple(item for item in items if isinstance(item, str))
    patterns = {item.name: item.pattern for item in items if not isinstance(item, str)}
    return Redactor(rules=rules, patterns=patterns)
