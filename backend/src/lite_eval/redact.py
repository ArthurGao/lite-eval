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

import bisect
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Protocol

from lite_eval.contracts import Output, RedactConfig, Score
from lite_eval.errors import ConfigError


def _mark(rule: str) -> str:
    return f"«redacted:{rule}»"


_MARK_TAIL = r"(?!«redacted)"  # never re-redact a marker

# Each pattern has a `pre` group (kept) and a `secret` group (replaced by the marker). Every run
# that a match may start at many positions is bounded (RFC lengths where one exists): an unbounded
# one made redaction quadratic on adversarial text (E1 security review, measured).
Spans = Callable[[str], list[tuple[int, int]]]  # secret spans, ascending, non-overlapping


def _regex_spans(pattern: re.Pattern[str]) -> Spans:
    return lambda text: [m.span("secret") for m in pattern.finditer(text)]


# scheme://user:PASSWORD@host. The password may hold an unencoded '@' or '/' and has no length
# limit, so it runs to the first '@' that is followed by a host and then a delimiter or the end,
# within the same whitespace-free token. One regex with a lazy run did this in quadratic time on
# text with many `scheme://user:` starts (E1 security review); here every step is linear and each
# start looks its end up by bisection.
_URL_START = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]{0,31}://[^\s:/@]{0,256}:")
_AT_HOST = re.compile(r"@[^\s@/?#]{1,255}(?=[/?#\s]|$)")
_SPACE = re.compile(r"\s")


def _url_password_spans(text: str) -> list[tuple[int, int]]:
    ats = [m.start() for m in _AT_HOST.finditer(text)]
    if not ats:
        return []
    spaces = [m.start() for m in _SPACE.finditer(text)]
    spans: list[tuple[int, int]] = []
    for m in _URL_START.finditer(text):
        start = m.end()
        if spans and start < spans[-1][1]:
            continue  # inside the password just found
        i = bisect.bisect_right(ats, start)  # an '@' after at least one password character
        if i == len(ats):
            break
        at = ats[i]
        j = bisect.bisect_left(spaces, start)
        if j < len(spaces) and spaces[j] < at:
            continue  # the '@' belongs to another token
        spans.append((start, at))
    return spans


_CONNECTION: tuple[Spans, ...] = (
    _url_password_spans,
    # ?sslpassword=VALUE&
    _regex_spans(
        re.compile(
            r"(?P<pre>[?&][\w.-]{0,64}password=)" + _MARK_TAIL + r"(?P<secret>[^\s&#]+)",
            re.IGNORECASE,
        )
    ),
    # password='a b' / PGPASSWORD=x / db_password = "y"
    _regex_spans(
        re.compile(
            r"(?P<pre>(?<![?&\w.-])\w*password\s*=\s*)"
            + _MARK_TAIL
            + r"(?P<secret>'(?:[^'\\]|\\.)*'|\"[^\"]*\"|[^\s'\"]+)",
            re.IGNORECASE,
        )
    ),
)

_API_KEYS = tuple(
    _regex_spans(p)
    for p in (
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
)

_EMAILS = (
    _regex_spans(
        re.compile(
            r"(?P<pre>)(?P<secret>\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}\b)"
        )
    ),
)

_BUILTIN: dict[str, tuple[Spans, ...]] = {
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
    def _compiled(self) -> tuple[tuple[str, str, Spans], ...]:
        """(rule, marker, spans) triples, built once per redactor."""
        out = [(rule, _mark(rule), s) for rule in self.rules for s in _BUILTIN[rule]]
        for name, pattern in self.patterns.items():
            try:
                out.append((name, _mark(name), _regex_spans(re.compile(f"(?P<secret>{pattern})"))))
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
        for _, marker, spans in self._compiled:
            found = spans(value)
            if found:
                parts, last = [], 0
                for start, end in found:
                    parts += [value[last:start], marker]
                    last = end
                value = "".join(parts) + value[last:]
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
            Finding(rule, value[start:end])
            for rule, _, spans in self._compiled
            for start, end in spans(value)
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
