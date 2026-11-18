"""lite-eval's contracts (design §5, §18).

Versioned with `schema_version`; fields are only ever added. Every record is frozen and rejects
unknown fields. A new field must have a default so that records written by an older
`schema_version` still load (tests/data/records/ holds committed samples).
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, model_validator

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
    # E5: thinking is part of the instrument (L4); usage is billed apart from the target's.
    think: bool | str | None = None
    usage: Usage | None = None


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
    license: str | None = None  # E4: an imported benchmark's terms travel with its runs


Verdict = Literal["value", "separate", "none"]
# How a scorer judges (E2): "value" -- its value is 1 exactly when the case passes (exact_match,
# hit@k); "separate" -- a fraction plus its own pass / fail (keywords): it gets a `:pass_rate`
# metric; "none" -- no pass / fail at all (mrr@k, a weighted composite). Comparisons pick McNemar
# or the bootstrap from this declaration, never from the values a run happened to produce.


class ScorerRef(_Record):
    name: str
    version: str
    verdict: Verdict | None = None  # None: recorded before E2
    # E5: for a model judge, whether a calibration run vouches for it (None: not a judge)
    calibrated: bool | None = None


class RedactConfig(_Record):
    rules: list[str] = Field(default_factory=list)
    patterns: dict[str, str] = Field(default_factory=dict)
    # Names of the secrets masked by value (E4d, design §18.12); the values are never recorded.
    known: list[str] = Field(default_factory=list)


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

    @property
    def errored_outputs(self) -> int:
        """Outputs that failed (every kind): the gate's `errors`, the index's column."""
        return sum(self.target_errors.values())

    @property
    def scoring_error_count(self) -> int:
        return sum(self.scoring_errors.values())


class CostSummary(_Record):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    usage_unknown: int = 0  # outputs whose usage was not reported
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    # E5: model judges, billed apart from the target (design §18.24)
    judge_calls: int = 0
    judge_input_tokens: int = 0
    judge_output_tokens: int = 0
    judge_reasoning_tokens: int = 0
    judge_cost_usd: float = 0.0


class RunWarning(_Record):
    code: str
    message: str


class CacheStats(_Record):
    hits: int = 0
    misses: int = 0


class CiRecord(_Record):
    ci_mode: bool = False
    paid_allowed: bool = False


# Lower-is-better gate metrics read from the cost summary (gate name -> CostSummary field).
COST_METRICS = {
    "latency_p50": "latency_p50_ms",
    "latency_p95": "latency_p95_ms",
    "cost_usd": "cost_usd",
}
# Gate metrics read from the run summary (E4d): errored outputs and scoring errors, bounded with
# `max` like a cost metric, never compared to a baseline.
RUN_METRICS = ("errors", "error_rate", "scoring_errors")


class GateRule(_Record):
    """Design §8.4, §18.19: an absolute bound (`min` / `max`) or a drop against the baseline."""

    metric: str
    min: float | None = None
    max: float | None = None
    compare_to: Literal["baseline"] | None = None
    max_drop: float | None = Field(default=None, ge=0)
    require_significant: bool = False
    baseline_note: str | None = None
    # The configs this rule judges (E4d); None: every config of the suite.
    configs: list[str] | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> "GateRule":
        absolute = self.min is not None or self.max is not None
        relative = self.compare_to is not None or self.max_drop is not None
        if absolute == relative:
            raise ValueError(
                f"gate rule for {self.metric!r} needs either min/max or compare_to + max_drop"
            )
        if relative and (self.compare_to is None or self.max_drop is None):
            raise ValueError(
                f"relative gate rule for {self.metric!r} needs compare_to and max_drop"
            )
        if self.require_significant and not relative:
            raise ValueError("require_significant only applies to a relative rule")
        if relative and (self.metric in COST_METRICS or self.metric in RUN_METRICS):
            raise ValueError(
                f"{self.metric} cannot be compared to the baseline: a cost or run metric is "
                "bounded with `max`"
            )
        return self

    def applies_to(self, config: str) -> bool:
        return self.configs is None or config in self.configs


class GateSpec(_Record):
    rules: list[GateRule] = Field(min_length=1)
    allow_partial: bool = False
    allow_partial_overlap: bool = False

    @property
    def relative(self) -> list[GateRule]:
        return [r for r in self.rules if r.compare_to is not None]


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
    gate: GateSpec | None = None  # as declared in the suite; `lite-eval run --gate` evaluates it
    case_hashes: dict[str, str] = Field(default_factory=dict)  # case id -> hash (§18.3)

    @property
    def config_name(self) -> str:
        """The suite config this run is ("" when not recorded); not a stored field."""
        return str(self.target.config.get("name", ""))


class MetricDelta(_Record):
    """One metric of a comparison (design §8.2, §18.2, §18.3)."""

    metric: str
    status: Literal["compared", "incomparable"]
    reason: str = ""  # why incomparable
    baseline: float | None = None
    candidate: float | None = None
    delta: float | None = None  # candidate - baseline, over the compared cases
    n: int = 0  # compared cases
    discordant: int = 0  # cases whose value differs
    test: Literal["mcnemar", "bootstrap"] | None = None
    p_value: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    significant: bool | None = None
    within_noise: bool = False
    note: str = ""  # the verdict in words, e.g. "not significant (n=20, 1 discordant case)"


CaseChangeKind = Literal["regressed", "fixed", "changed"]


class CaseChange(_Record):
    case_id: str
    metric: str
    kind: CaseChangeKind  # regressed / fixed: a pass flipped; changed: a continuous value moved
    baseline: float
    candidate: float
    # E3: which way the case moved, decided here so no reader re-derives it (scores: higher is
    # better). None in a comparison serialised before E3.
    direction: Literal["worse", "better"] | None = None


class Comparison(_Record):
    schema_version: int = SCHEMA_VERSION
    baseline_run: str
    candidate_run: str
    baseline_config: str = ""  # E3: the configs' names, so a reader need not load both records
    candidate_config: str = ""
    dataset: str
    overlap: Literal["full", "partial_overlap"]
    changed_cases: list[str] = Field(default_factory=list)  # same id, different content
    added_cases: list[str] = Field(default_factory=list)
    removed_cases: list[str] = Field(default_factory=list)
    case_weight: float  # one compared case moves a 0..1 metric by this much
    metrics: list[MetricDelta]
    changes: list[CaseChange]  # regressions first
    warnings: list[RunWarning] = Field(default_factory=list)


NOTE_MAX_BYTES = 4096


def _note_fits(note: str) -> str:
    # §18.9: 4 KB of UTF-8, not 4096 characters (CJK text is three bytes a character).
    if len(note.encode("utf-8")) > NOTE_MAX_BYTES:
        raise ValueError(f"a note is at most {NOTE_MAX_BYTES} bytes of UTF-8")
    return note


Note = Annotated[str, AfterValidator(_note_fits)]


class Annotation(_Record):
    """Design §18.9. Written by the Viewer and `lite-eval triage` (E3)."""

    schema_version: int = SCHEMA_VERSION
    annotation_id: str
    dataset: str
    case_id: str
    case_hash: str
    run_id: str | None = None
    category: AnnotationCategory
    note: Note = ""
    author: str
    created_at: datetime
    supersedes: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        """One current annotation per key (§18.9)."""
        return (self.dataset, self.case_id, self.case_hash)


class BaselineChange(_Record):
    """One `lite-eval baseline set`, appended to `<root>/baselines.log.jsonl` (design §12): the
    Viewer's trends mark where a suite's baseline switched."""

    schema_version: int = SCHEMA_VERSION
    suite: str
    run_id: str
    previous: str | None = None
    set_at: datetime
