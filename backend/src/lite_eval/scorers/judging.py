"""The judging pack's scorer (design §7, §9): `label_agreement`, the judge's verdict against the
label fixed by construction. Its mean is the judge's agreement; `analysis.judging.confusion`
reads the (expected, got) pairs from its evidence."""

from dataclasses import dataclass
from typing import Any

from lite_eval.contracts import Case, Output, Score, Verdict
from lite_eval.errors import ConfigError
from lite_eval.scorers.base import Spec, make_score


def _label(value: Any, where: str) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("label"), str):
        raise ValueError(f"{where} must be {{label: str}}")
    return str(value["label"])


@dataclass(frozen=True)
class LabelAgreement:
    spec: Spec = "label_agreement"
    name: str = "label_agreement"
    version: str = "1"

    @property
    def verdict(self) -> Verdict:
        return "value"

    def score(self, case: Case, output: Output) -> Score:
        expected = _label(case.expected, "expected")
        got = _label(output.value, "the judge's output")
        ok = got == expected
        return make_score(
            self,
            output,
            value=float(ok),
            passed=ok,
            reason="agrees" if ok else f"expected {expected}, got {got}",
            evidence={"expected": expected, "got": got},
        )


def judging_scorer(kind: str, params: dict[str, Any]) -> LabelAgreement | None:
    if kind != "label_agreement":
        return None
    if params:
        raise ConfigError(f"label_agreement takes no params, got {sorted(params)}")
    return LabelAgreement()
