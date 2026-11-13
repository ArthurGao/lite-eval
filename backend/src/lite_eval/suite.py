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
from lite_eval.errors import ConfigError

_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _slug(value: str, what: str) -> str:
    if not _SLUG.match(value):
        raise ValueError(f"{what} {value!r} must match {_SLUG.pattern} (it names a directory)")
    return value


class _Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TargetSpec(_Spec):
    python: str
    configs: list[dict[str, JsonValue]] = Field(min_length=1)

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
    gate: JsonValue = None  # recorded now, evaluated from E2

    @field_validator("name")
    @classmethod
    def _name(cls, name: str) -> str:
        return _slug(name, "suite name")

    @model_validator(mode="after")
    def _unique_config_names(self) -> "SuiteSpec":
        names = [str(c["name"]) for t in self.targets for c in t.configs]
        if len(set(names)) != len(names):
            raise ValueError(f"config names must be unique across targets: {names}")
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
    if spec.pack:  # the pack registry arrives with the retrieval pack (E2)
        raise ConfigError(f"{path}: pack {spec.pack!r} is not available yet (E1 has only `cost`)")
    return Suite(
        spec=spec,
        path=path,
        root=(root or default_root(path)).resolve(),
        sha256=sha256_hex(raw),
    )
