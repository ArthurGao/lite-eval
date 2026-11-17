"""Whether a calibration run vouches for a judge (design §9, §18.24).

A judge scorer names `calibration: {run, min_agreement}`; before any call the run must exist, be a
complete judging-pack run of this very judge (the same `Rubric.identity`: model, route,
temperature, thinking, rubric, prompt version) and have agreed with its labels at least
`min_agreement` of the time. Every failure is a ConfigError (exit 2).
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lite_eval.errors import ConfigError
from lite_eval.judges.target import JudgeTarget
from lite_eval.store import read_record


@dataclass(frozen=True)
class Calibration:
    run: str  # a run directory, relative to the evaluation root
    min_agreement: float = 1.0

    @classmethod
    def parse(cls, value: Any) -> "Calibration":
        if not isinstance(value, dict) or not isinstance(value.get("run"), str):
            raise ConfigError("judge scorer: calibration must be {run, min_agreement}")
        unknown = set(value) - {"run", "min_agreement"}
        if unknown:
            raise ConfigError(f"judge scorer: calibration has unknown keys {sorted(unknown)}")
        minimum = value.get("min_agreement", 1.0)
        # bool is an int: `true` is not a threshold; NaN fails every comparison, so it is refused
        if isinstance(minimum, bool) or not isinstance(minimum, int | float):
            minimum = math.nan
        if not 0.0 <= minimum <= 1.0:
            raise ConfigError(
                f"judge scorer: calibration min_agreement must be a number from 0 to 1, "
                f"got {value.get('min_agreement')!r}"
            )
        return cls(run=value["run"], min_agreement=float(minimum))

    def check(self, who: str, identity: dict[str, Any], root: Path) -> None:
        directory = root / self.run
        if not (directory / "run.json").is_file():
            raise ConfigError(f"judge {who}: calibration run {self.run} not found under {root}")
        rec = read_record(directory)
        agreement = rec.summary.metrics.get("label_agreement")
        if rec.target.name != JudgeTarget.name or agreement is None:
            raise ConfigError(f"judge {who}: {self.run} is not a judging-pack run")
        theirs = rec.target.config
        diffs = [
            f"{k} {theirs.get(k)!r} -> {v!r}" for k, v in identity.items() if theirs.get(k) != v
        ]
        if diffs:
            raise ConfigError(
                f"judge {who}: run {rec.run_id} calibrated a different judge: " + ", ".join(diffs)
            )
        if rec.status != "complete":
            raise ConfigError(f"judge {who}: calibration run {rec.run_id} is {rec.status}")
        if agreement.value < self.min_agreement - 1e-9:
            raise ConfigError(
                f"judge {who}: calibration run {rec.run_id} agreement "
                f"{agreement.value:.4f} < {self.min_agreement}"
            )
