"""A minimal Python target: looks answers up in a table. `python: examples.demo_target:make`."""

from typing import Any

from lite_eval.contracts import Billing, Case, Output, Span, Trace, Usage

ANSWERS = {"New Zealand": "Wellington", "Australia": "Canberra", "Japan": "Tokyo"}


class Lookup:
    name = "capital-lookup"

    def __init__(self, fuzzy: bool = False) -> None:
        self.fuzzy = fuzzy

    def config(self) -> dict[str, Any]:
        return {"fuzzy": self.fuzzy}

    def versions(self) -> dict[str, str]:
        return {"table": "2026-11-13"}

    def info(self) -> dict[str, str]:
        return {}

    def billing(self) -> Billing:
        return "free"

    def setup_identity(self) -> dict[str, Any]:
        return {}

    async def setup(self, dataset: Any) -> None:
        pass

    async def teardown(self) -> None:
        pass

    async def run(self, case: Case) -> Output:
        assert isinstance(case.input, dict)
        country = str(case.input["country"])
        key = country.title() if self.fuzzy else country
        answer = ANSWERS.get(key)
        decision = (
            Span(kind="decision", attrs={"feature": "normalise", "op": key}) if self.fuzzy else None
        )
        return Output(
            case_id=case.id,
            value=answer,
            trace=Trace(
                spans=[decision] if decision else [],
                usage=Usage(input_tokens=len(country)),
                latency_scope="table lookup",
            ),
        )


def make(fuzzy: bool = False) -> Lookup:
    return Lookup(fuzzy=fuzzy)
