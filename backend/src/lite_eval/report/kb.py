"""Run records as rows of the KB's `global/llm-evals/results.jsonl` (design §13, §18.24).

The schema is the KB README's "`results.jsonl` schema" as of 2026-11-18 (the fields and value
sets its `scripts/check.mjs` enforces); every row is checked against it here, so the export never
writes a row the KB would reject. One row per run and exported metric. What a run record cannot
know (the eval card, project, task archetype, dataset shape, grader, trust, notes) comes from the
suite's `kb:` section; vendor, model and effort come from it too, or from the target (a judge
target's provider, model and thinking).
"""

import json
import re
from collections.abc import Sequence
from typing import Any, get_args
from zoneinfo import ZoneInfo

from lite_eval.contracts import RunRecord
from lite_eval.errors import ConfigError
from lite_eval.suite import KbEffort, KbSpec

KB_SCHEMA = "KB global/llm-evals README `results.jsonl` schema, 2026-11-18"
EFFORTS = set(get_args(KbEffort))
REQUIRED = ("eval", "date", "project", "task", "dataset", "vendor", "model", "effort", "grader",
            "metric", "value", "n", "trials", "trust", "notes")  # fmt: skip


def _effort(think: Any) -> str:
    """A judge's thinking as the KB's effort: a level as named, on = the model's default."""
    if isinstance(think, str):
        return think
    return "default" if think else "n/a"


def _who(rec: RunRecord, spec: KbSpec) -> tuple[Any, Any, Any]:
    t = rec.target
    config, info = t.config, t.info
    vendor = spec.vendor or config.get("vendor") or info.get("vendor") or config.get("provider")
    model = spec.model or config.get("model") or info.get("model")
    effort = spec.effort or config.get("effort")
    if effort is None and "think" in config:
        effort = _effort(config["think"])
    return vendor, model, effort


def _problems(row: dict[str, Any]) -> list[str]:
    """What KbSpec cannot vouch for: fields taken from the record (vendor, model, an effort
    derived from the target) and the row as a whole."""
    problems = [f"missing {f}" for f in REQUIRED if row.get(f) in (None, "")]
    if row.get("effort") not in EFFORTS:
        problems.append(f"effort {row.get('effort')!r} is not one of {sorted(EFFORTS)}")
    if row.get("trust") == "contested" and not re.search(r"[a-z]", str(row.get("notes", ""))):
        problems.append("a contested row needs its reason in notes")
    return problems


def kb_rows(records: Sequence[RunRecord], spec: KbSpec) -> list[dict[str, Any]]:
    rows = []
    for rec in records:
        vendor, model, effort = _who(rec, spec)
        n = rec.summary.n_cases
        cost = rec.cost.cost_usd + rec.cost.judge_cost_usd
        for metric, kb_metric in spec.metrics.items():
            summary = rec.summary.metrics.get(metric)
            if summary is None:
                raise ConfigError(f"run {rec.run_id} has no metric {metric!r} to export")
            row: dict[str, Any] = {
                "eval": spec.eval,
                "date": rec.started_at.astimezone(ZoneInfo(spec.timezone)).date().isoformat(),
                "project": spec.project,
                "task": spec.task,
                "dataset": spec.dataset,
                "vendor": vendor,
                "model": model,
                "effort": effort,
                "variant": rec.config_name,
                "grader": spec.grader,
                "metric": kb_metric,
                "value": round(summary.value, 4),
            }
            if rec.repeats > 1:
                row["values"] = [round(v, 4) for v in summary.per_repeat]
            row |= {"n": n, "trials": rec.repeats}
            if cost > 0 and n:
                row["cost_usd"] = round(cost / n, 6)
            row |= {"trust": spec.trust, "notes": spec.notes}
            problems = _problems(row)
            if problems:
                raise ConfigError(
                    f"run {rec.run_id}, {metric}: the KB would reject this row ({KB_SCHEMA}): "
                    + "; ".join(problems)
                )
            rows.append(row)
    return rows


def render_rows(rows: Sequence[dict[str, Any]]) -> str:
    """JSON Lines as the KB keeps them: compact, UTF-8, one row per line."""
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)
