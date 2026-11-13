"""A scripted target for tests and examples. Never calls anything."""

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import JsonValue

from lite_eval.contracts import Billing, Case, ErrorKind, Output, Span, Trace, Usage
from lite_eval.targets.base import TargetError, TransportError, current_repeat

if TYPE_CHECKING:
    from lite_eval.datasets.local import Dataset


@dataclass
class Step:
    """What the fake does for one case. Unset `value` echoes `case.expected`."""

    value: JsonValue = None
    echo_expected: bool = True
    error: ErrorKind | None = None
    raise_exception: bool = False
    transport_failures: int = 0  # raise TransportError this many times first
    usage: Usage | None = field(default_factory=lambda: Usage(input_tokens=10, output_tokens=5))
    latency_ms: float | None = 12.0
    delay_s: float = 0.0
    spans: list[Span] = field(default_factory=list)


class FakeTarget:
    def __init__(
        self,
        name: str = "fake",
        *,
        steps: dict[str, Step] | None = None,
        default: Step | None = None,
        config: dict[str, Any] | None = None,
        versions: dict[str, str] | None = None,
        billing: Billing = "free",
        deterministic: bool = True,
        setup_identity: dict[str, Any] | None = None,
        per_repeat: dict[tuple[str, int], Step] | None = None,
    ) -> None:
        self.name = name
        self.steps = steps or {}
        self.default = default or Step()
        self.per_repeat = per_repeat or {}
        self._config = config if config is not None else {"mode": "echo"}
        self._versions = versions if versions is not None else {"fake": "1"}
        self._billing: Billing = billing
        self.deterministic = deterministic
        self._setup_identity = setup_identity or {}
        self.calls: Counter[str] = Counter()
        self.setups = 0
        self.teardowns = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self._attempts: Counter[tuple[str, int]] = Counter()

    def config(self) -> dict[str, Any]:
        return dict(self._config)

    def versions(self) -> dict[str, str]:
        return dict(self._versions)

    def info(self) -> dict[str, str]:
        return {"host": "fake"}

    def billing(self) -> Billing:
        return self._billing

    def setup_identity(self) -> dict[str, Any]:
        return dict(self._setup_identity)

    async def setup(self, dataset: "Dataset") -> None:
        self.setups += 1

    async def teardown(self) -> None:
        self.teardowns += 1

    async def run(self, case: Case) -> Output:
        self.calls[case.id] += 1
        repeat = current_repeat.get()
        step = self.per_repeat.get((case.id, repeat)) or self.steps.get(case.id) or self.default
        self._attempts[(case.id, repeat)] += 1
        if self._attempts[(case.id, repeat)] <= step.transport_failures:
            raise TransportError(f"connection reset ({case.id})")
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if step.delay_s:
                await asyncio.sleep(step.delay_s)
        finally:
            self.in_flight -= 1
        if step.raise_exception:
            raise RuntimeError(f"boom on {case.id}")
        value = case.expected if step.echo_expected and step.value is None else step.value
        if step.error is not None:
            raise TargetError(
                step.error,
                f"scripted {step.error} on {case.id}",
                usage=step.usage,
                latency_ms=step.latency_ms,
                value=value if step.error == "truncated" else None,
            )
        return Output(
            case_id=case.id,
            value=value,
            trace=Trace(
                spans=list(step.spans),
                usage=step.usage,
                latency_ms=step.latency_ms,
                latency_scope="scripted",
            ),
        )


def from_config(**params: Any) -> FakeTarget:
    """Factory for suites (`python: lite_eval.targets.fake:from_config`): echoes `expected`."""
    name = str(params.pop("target_name", "fake"))
    return FakeTarget(name, config=params)
