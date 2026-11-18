"""Memory scorers (design §7 memory pack, §18.16 lite-mem, §18.23).

The output value (or `field` inside it) is the list of memories the system recalled: strings, or
dicts whose text is under `text` (default `content`). `expected` (or `expected` inside it) is
`{hops: [[keyword, ...], ...]}`.

A hop is found when one memory contains every keyword of the hop, compared with `str.lower` and
nothing else (lite-mem `eval/multihop.py` `_has`, copied character for character: no casefold, no
whitespace collapsing). Keywords split across two memories do not count.

`hop<n>` scores hop n (from 1) and records the memory that held it; `all_hops` passes when every
hop is found. Each is 0 or 1 per case, so a run's mean times its case count is lite-mem's count.
Evidence recall against session or turn ids uses the retrieval pack's `recall@k`.
"""

import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from lite_eval.contracts import Case, Output, Score, Verdict
from lite_eval.errors import ConfigError
from lite_eval.scorers.base import Spec, make_score, spec_name
from lite_eval.scorers.deterministic import get_path

NAME = re.compile(r"^(?:hop([1-9][0-9]*)|all_hops)$")
_PARAMS = {"field", "expected", "text"}


def _found(memories: list[str], keywords: list[str]) -> str | None:
    """The first memory holding every keyword (lite-mem `_has`: `k.lower() in m.lower()`,
    each side lowered once; returns the memory)."""
    keys = [k.lower() for k in keywords]
    for m in memories:
        lowered = m.lower()
        if all(k in lowered for k in keys):
            return m
    return None


@dataclass(frozen=True)
class HopScorer:
    hop: int | None  # None: all_hops
    params: dict[str, Any] = field(default_factory=dict)
    version: str = "1"

    @property
    def spec(self) -> Spec:
        name = "all_hops" if self.hop is None else f"hop{self.hop}"
        return name if not self.params else {name: self.params}

    @cached_property
    def name(self) -> str:
        return spec_name(self.spec)

    @property
    def verdict(self) -> Verdict:
        return "value"

    def _memories(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("memory output must be a list of memories")
        key = self.params.get("text", "content")
        texts = []
        for item in value:
            text = item.get(key) if isinstance(item, dict) else item
            if not isinstance(text, str):
                raise ValueError(f"a memory must be a string or a dict with a string {key!r}")
            texts.append(text)
        return texts

    def score(self, case: Case, output: Output) -> Score:
        memories = self._memories(get_path(output.value, self.params.get("field")))
        expected = get_path(case.expected, self.params.get("expected"))
        hops = expected.get("hops") if isinstance(expected, dict) else None
        if not isinstance(hops, list) or not all(isinstance(h, list) for h in hops):
            raise ValueError("memory expected must be {hops: [[keyword, ...], ...]}")
        hops = [[str(k) for k in hop] for hop in hops]
        evidence: dict[str, Any]
        if self.hop is None:
            hit = [_found(memories, hop) is not None for hop in hops]
            ok = bool(hit) and all(hit)
            reason, evidence = f"{sum(hit)}/{len(hit)} hops", {"hops": hit}
        else:
            if self.hop > len(hops):
                raise ValueError(f"hop{self.hop}: the case has {len(hops)} hops")
            memory = _found(memories, hops[self.hop - 1])
            ok = memory is not None
            reason, evidence = ("found" if ok else "not found"), {"memory": memory}
        return make_score(
            self,
            output,
            value=float(ok),
            passed=ok,
            reason=reason,
            evidence=evidence,
        )


def memory_scorer(name: str, params: dict[str, Any]) -> HopScorer | None:
    """A HopScorer when `name` is `hop<n>` or `all_hops`, else None."""
    m = NAME.match(name)
    if m is None:
        return None
    unknown = set(params) - _PARAMS
    if unknown:
        raise ConfigError(f"scorer {name!r}: unknown params {sorted(unknown)}")
    return HopScorer(hop=int(m.group(1)) if m.group(1) else None, params=dict(params))
