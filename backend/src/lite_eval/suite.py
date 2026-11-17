"""Suite files (design §11.1, §18.20)."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from lite_eval.canonical import sha256_hex
from lite_eval.contracts import GateSpec
from lite_eval.errors import ConfigError
from lite_eval.judges.spec import JudgeSpec
from lite_eval.packs import get_pack
from lite_eval.targets.command import CommandSpec
from lite_eval.targets.http import HttpSpec

_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _slug(value: str, what: str) -> str:
    if not _SLUG.match(value):
        raise ValueError(f"{what} {value!r} must match {_SLUG.pattern} (it names a directory)")
    return value


class _Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TargetSpec(_Spec):
    """One `targets:` entry: exactly one access method, and its configs (design §18.23)."""

    python: str | None = None
    http: HttpSpec | None = None
    command: CommandSpec | None = None
    configs: list[dict[str, JsonValue]] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_method(self) -> "TargetSpec":
        given = [m for m in ("python", "http", "command") if getattr(self, m) is not None]
        if len(given) != 1:
            raise ValueError(f"a target needs exactly one of python, http, command; got {given}")
        return self

    @field_validator("configs")
    @classmethod
    def _names(cls, configs: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
        for c in configs:
            name = c.get("name")
            if not isinstance(name, str):
                raise ValueError(f"every config needs a string `name`: {c}")
            _slug(name, "config name")
        return configs


class RedactRule(_Spec):
    name: str
    pattern: str


class Budget(_Spec):
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_calls: int | None = Field(default=None, ge=0)


KbTask = Literal["extraction", "judging", "drafting", "summarisation", "chat"]
KbEffort = Literal["low", "medium", "high", "default", "n/a"]
KbTrust = Literal["measured", "contested", "anecdotal"]
KB_GRADER = r"^(labels|constructed|count|judge:.+|consensus:.+)$"


class KbSpec(BaseModel):
    """A suite's `kb:` section (design §18.24): what a KB llm-evals results row needs and a run
    record cannot know. The value sets are the KB README's (`scripts/check.mjs`), so a wrong one
    fails when the suite loads, not at export."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    eval: str
    project: str
    task: KbTask
    dataset: str
    grader: str = Field(pattern=KB_GRADER)
    metrics: dict[str, str] = Field(min_length=1)  # lite-eval metric -> KB metric name
    trust: KbTrust = "measured"
    notes: str
    vendor: str | None = None
    model: str | None = None
    effort: KbEffort | None = None


class SuiteSpec(_Spec):
    schema_version: Literal[1] = 1
    name: str
    pack: str | None = None
    dataset: str
    targets: list[TargetSpec] = Field(min_length=1)
    scorers: list[str | dict[str, Any]] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)  # selection for reports; used from E2
    repeats: int | None = Field(default=None, ge=1)  # None: 1, or 2 for a non-deterministic target
    concurrency: int = Field(default=4, ge=1)
    timeout_s: float = Field(default=120.0, gt=0)
    seed: int = 0
    budget: Budget = Field(default_factory=Budget)
    redact: list[str | RedactRule] = Field(default_factory=list)
    expect_features: list[str] = Field(default_factory=list)
    expect_tags: dict[str, list[str]] = Field(default_factory=dict)
    allow_paid_in_ci: bool = False
    cache: Literal["auto", "on", "off"] = "auto"
    gate: GateSpec | None = None
    # Model judges (design §9, §18.24): role -> model; `judge` scorers name a role.
    judges: dict[str, JudgeSpec] = Field(default_factory=dict)
    judge_timeout_s: float = Field(default=120.0, gt=0)
    kb: KbSpec | None = None  # `lite-eval export kb`: the KB results row's fields (§18.24)

    @field_validator("gate", mode="before")
    @classmethod
    def _gate_list(cls, gate: Any) -> Any:
        """The design's short form: a bare list of rules."""
        return {"rules": gate} if isinstance(gate, list) else gate

    @field_validator("name")
    @classmethod
    def _name(cls, name: str) -> str:
        return _slug(name, "suite name")

    @model_validator(mode="after")
    def _unique_config_names(self) -> "SuiteSpec":
        names = [str(c["name"]) for t in self.targets for c in t.configs]
        if len(set(names)) != len(names):
            raise ValueError(f"config names must be unique across targets: {names}")
        for rule in self.gate.rules if self.gate else []:
            unknown = sorted(set(rule.configs or []) - set(names))
            if unknown:
                raise ValueError(f"gate rule names config(s) {unknown}, not in this suite: {names}")
        return self


@dataclass(frozen=True)
class Suite:
    spec: SuiteSpec
    path: Path
    root: Path  # the evaluation root: runs/, .lite-eval/, annotations.jsonl live here
    sha256: str

    @property
    def dataset_path(self) -> Path:
        return (self.path.parent / self.spec.dataset).resolve()

    @property
    def scorer_specs(self) -> list[str | dict[str, Any]]:
        """The suite's scorers, or its pack's defaults."""
        if self.spec.scorers:
            return list(self.spec.scorers)
        return list(get_pack(str(self.spec.pack)).scorers)

    @property
    def import_root(self) -> Path:
        """Where `module:factory` targets are imported from: the repository holding the root."""
        return self.root.parent


def default_root(suite_path: Path) -> Path:
    parent = suite_path.parent
    return parent.parent if parent.name == "suites" else parent


def load_suite(path: Path, root: Path | None = None) -> Suite:
    path = path.resolve()
    if not path.is_file():
        raise ConfigError(f"suite file not found: {path}")
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw)
        spec = SuiteSpec.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"{path}: invalid suite: {exc}") from exc
    if not spec.pack and not spec.scorers:
        raise ConfigError(f"{path}: a suite without `pack` must list `scorers`")
    if spec.pack:
        try:
            get_pack(spec.pack)
        except ConfigError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    return Suite(
        spec=spec,
        path=path,
        root=(root or default_root(path)).resolve(),
        sha256=sha256_hex(raw),
    )
