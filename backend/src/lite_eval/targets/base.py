"""The system under test (design §5.3, §18.8)."""

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from lite_eval.contracts import Billing, Case, ErrorKind, Output, Usage

if TYPE_CHECKING:
    from lite_eval.datasets.local import Dataset

# The repeat index (from 0) of the output being generated, set by the runner around each
# `run(case)` call. A target that seeds or varies by repeat reads it; the protocol stays
# `run(case)`.
current_repeat: ContextVar[int] = ContextVar("lite_eval_current_repeat", default=0)
# True during the untimed warm-up call (design §18.18), whose output is discarded.
warming_up: ContextVar[bool] = ContextVar("lite_eval_warming_up", default=False)


@runtime_checkable
class Target(Protocol):
    """One configuration of the system under test.

    Optional members, read by the runner with getattr (absent means the default):
    - `deterministic: bool` (True): a non-deterministic target is run twice per case unless the
      suite sets `repeats` (design §6);
    - `async share_setup_from(leader)`: reuse a leader's setup in the same setup group (§18.18);
    - `serial: bool` (False): the runner sends one case at a time, e.g. one subprocess (§18.11);
    - `abandoned() -> str | None`: why the target stopped running cases; the run is partial;
    - `artifacts() -> dict[str, str]`: text files for the run directory, redacted by the runner;
    - `describe() -> str`: the command it runs or the URL it calls, announced before a run.
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


MB = 1024 * 1024


class SpecBase(BaseModel):
    """What every suite-configured target block (`http:`, `command:`) declares (design §18.23)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    billing: Billing = "unknown"
    versions: dict[str, str] = Field(default_factory=dict)  # what changes the output; cache key
    deterministic: bool = True


class SpecIdentity:
    """Identity of a target built from a suite block: versions, billing and determinism come
    from the block; `config()` (the subclass's) is the params plus the block's request shape.
    Each config sets up alone: nothing is built from the dataset that another could share."""

    name: str
    spec: SpecBase
    params: dict[str, JsonValue]

    @property
    def deterministic(self) -> bool:
        return self.spec.deterministic

    def config(self) -> dict[str, Any]:
        raise NotImplementedError

    def versions(self) -> dict[str, str]:
        return dict(self.spec.versions)

    def info(self) -> dict[str, str]:
        return {}

    def billing(self) -> Billing:
        return self.spec.billing

    def setup_identity(self) -> dict[str, Any]:
        return {self.name: self.config()}
