"""lite-eval's contracts (design §5, §18).

Versioned with `schema_version`; fields are only ever added. Every record is frozen and rejects
unknown fields. A new field must have a default so that records written by an older
`schema_version` still load (tests/data/records/ holds committed samples).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from lite_eval.canonical import content_hash

SCHEMA_VERSION = 1

ErrorKind = Literal["timeout", "exception", "truncated", "invalid_output", "http_status"]
ScoreErrorKind = Literal["scorer_exception", "invalid_judge_output", "judge_call_failed"]
Billing = Literal["free", "subscription", "per_token", "unknown"]
RunStatus = Literal["complete", "partial", "failed"]
AnnotationCategory = Literal["real_miss", "label_wrong", "excluded", "unclear"]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Provenance(_Record):
    source: str | None = None
    synthetic: bool = False
    author: str | None = None
    created: str | None = None
    license: str | None = None


class Case(_Record):
    id: str
    input: JsonValue
    expected: JsonValue = None
    tags: dict[str, str] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)

    def content_hash(self) -> str:
        """Design §18.6: covers id, input, expected and tags; not provenance."""
        return content_hash(
            {"id": self.id, "input": self.input, "expected": self.expected, "tags": self.tags}
        )


class Usage(_Record):
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0


class Span(_Record):
    kind: str
    name: str = ""
    start_ms: float = 0.0
    duration_ms: float = 0.0
    input: JsonValue = None
    output: JsonValue = None
    attrs: dict[str, JsonValue] = Field(default_factory=dict)


class Trace(_Record):
    spans: list[Span] = Field(default_factory=list)
    usage: Usage | None = None  # None: unknown (e.g. an uncaught exception), not zero
    latency_ms: float | None = None
    latency_scope: str | None = None


class ErrorInfo(_Record):
    kind: ErrorKind
    message: str


class Output(_Record):
    schema_version: int = SCHEMA_VERSION
    case_id: str
    repeat: int = 0
    value: JsonValue = None
    trace: Trace = Field(default_factory=Trace)
    error: ErrorInfo | None = None


class JudgeRecord(_Record):
    role: str
    model: str
    channel: str
    prompt_version: str
    temperature: float
    raw: JsonValue = None


class ScoreError(_Record):
    kind: ScoreErrorKind
    message: str


class Score(_Record):
    schema_version: int = SCHEMA_VERSION
    case_id: str
    repeat: int = 0
    scorer: str
    scorer_version: str
    value: float = Field(ge=0.0, le=1.0)
    passed: bool | None = None
    reason: str = ""
    evidence: JsonValue = None
    judge: JudgeRecord | None = None
    error: ScoreError | None = None


class SuiteRef(_Record):
    name: str
    path: str | None = None
    sha256: str | None = None


class TargetRecord(_Record):
    name: str
    config: dict[str, JsonValue]
    versions: dict[str, str] = Field(default_factory=dict)
    info: dict[str, str] = Field(default_factory=dict)
    billing: Billing = "unknown"


class DatasetRef(_Record):
    name: str
    version: str
    hash: str
    pack: str | None = None
    synthetic: bool = False


class ScorerRef(_Record):
    name: str
    version: str


class RedactConfig(_Record):
    rules: list[str] = Field(default_factory=list)
    patterns: dict[str, str] = Field(default_factory=dict)


class MetricSummary(_Record):
    value: float
    min: float
    max: float
    per_repeat: list[float]


class RunSummary(_Record):
    n_cases: int
    repeats: int
    case_weight: float  # how much one case moves a 0..1 metric: 1 / n_cases
    outputs_expected: int  # cases x repeats: the denominator, always
    outputs_recorded: int
    metrics: dict[str, MetricSummary] = Field(default_factory=dict)
    groups: dict[str, dict[str, dict[str, float]]] = Field(default_factory=dict)
    target_errors: dict[str, int] = Field(default_factory=dict)
    scoring_errors: dict[str, int] = Field(default_factory=dict)


class CostSummary(_Record):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    usage_unknown: int = 0  # outputs whose usage was not reported
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None


class RunWarning(_Record):
    code: str
    message: str


class CacheStats(_Record):
    hits: int = 0
    misses: int = 0


class CiRecord(_Record):
    ci_mode: bool = False
    paid_allowed: bool = False


class RunRecord(_Record):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    suite: SuiteRef
    target: TargetRecord
    dataset: DatasetRef
    scorers: list[ScorerRef]
    repeats: int
    seed: int
    concurrency: int
    timeout_s: float
    redact: RedactConfig = Field(default_factory=RedactConfig)
    started_at: datetime
    finished_at: datetime
    host: str
    lite_eval_version: str
    git_sha: str | None = None
    git_dirty: bool | None = None
    status: RunStatus
    summary: RunSummary
    cost: CostSummary
    warnings: list[RunWarning] = Field(default_factory=list)
    cache: CacheStats = Field(default_factory=CacheStats)
    ci: CiRecord = Field(default_factory=CiRecord)
    rescored_from: str | None = None
    gate: JsonValue = None  # recorded as declared; evaluated from E2


class Annotation(_Record):
    """Design §18.9. Written by the Viewer and `lite-eval triage` (E3)."""

    schema_version: int = SCHEMA_VERSION
    annotation_id: str
    dataset: str
    case_id: str
    case_hash: str
    run_id: str | None = None
    category: AnnotationCategory
    note: str = Field(default="", max_length=4096)
    author: str
    created_at: datetime
    supersedes: str | None = None
