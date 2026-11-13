"""The system under test (design §5.3, §18.8)."""

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import JsonValue

from lite_eval.contracts import Billing, Case, ErrorKind, Output, Usage

if TYPE_CHECKING:
    from lite_eval.datasets.local import Dataset

# The repeat index (from 0) of the output being generated, set by the runner around each
# `run(case)` call. A target that seeds or varies by repeat reads it; the protocol stays
# `run(case)`.
current_repeat: ContextVar[int] = ContextVar("lite_eval_current_repeat", default=0)


@runtime_checkable
class Target(Protocol):
    """One configuration of the system under test.

    Optional attribute `deterministic: bool` (default True): a non-deterministic target is run twice
    per case unless the suite sets `repeats` (design §6).
    """

    name: str

    def config(self) -> dict[str, Any]:
        """Everything that decides the output (model, thinking / effort, parameters); cache key."""
        ...

    def versions(self) -> dict[str, str]:
        """Versions that change the output (engine, model snapshot, prompt); cache key."""
        ...

    def info(self) -> dict[str, str]:
        """Recorded only (host, channel); not in the cache key."""
        ...

    def billing(self) -> Billing: ...

    def setup_identity(self) -> dict[str, Any]: ...

    async def setup(self, dataset: "Dataset") -> None: ...

    async def run(self, case: Case) -> Output: ...

    async def teardown(self) -> None: ...


class TargetError(Exception):
    """A failure the target reports, with what it knows about usage and latency (design §18.8).

    `value` keeps the part already produced (e.g. a reply cut at max_tokens, kind="truncated").
    """

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        usage: Usage | None = None,
        latency_ms: float | None = None,
        value: JsonValue = None,
    ) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.message = message
        self.usage = usage
        self.latency_ms = latency_ms
        self.value = value


class TransportError(Exception):
    """A transport-level failure (connection reset, 503 from a proxy). The only error retried."""
