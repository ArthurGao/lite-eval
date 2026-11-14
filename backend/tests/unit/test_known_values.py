"""Known secrets masked by value, in every form a reply may repeat them (design §18.12, E4d)."""

import html
import json
import time
import urllib.parse

import pytest

from lite_eval.redact import KnownValues, Redactor

SECRET = 'sk/a"b c+d=e&f<g>-0123456789'  # every encoding below changes it


def forms(value: str) -> list[str]:
    q, qp = urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)
    j = json.dumps(value)[1:-1]
    return [value, j, j.replace("/", "\\/"), html.escape(value), q, qp, q.lower(), qp.lower()]


@pytest.mark.parametrize("form", forms(SECRET))
def test_each_form_of_a_value_is_masked_with_its_name(form: str) -> None:
    known = KnownValues({"env:TOKEN": SECRET})
    assert known.text(f"before {form} after") == "before «redacted:env:TOKEN» after"


def test_a_value_containing_another_is_masked_whole() -> None:
    known = KnownValues({"env:SHORT": "abcdefgh", "env:LONG": "abcdefgh-ijklmnop"})
    assert known.text("x abcdefgh-ijklmnop y abcdefgh") == (
        "x «redacted:env:LONG» y «redacted:env:SHORT»"
    )


def test_empty_values_mask_nothing_and_short_ones_are_named() -> None:
    known = KnownValues({"env:EMPTY": "", "env:PIN": "1234", "env:KEY": "a-long-secret"})
    assert known.text("1234 and a-long-secret") == "«redacted:env:PIN» and «redacted:env:KEY»"
    assert known.short() == ["env:PIN"]  # masked anyway; the run warns (never the value)
    assert known.names() == ["env:KEY", "env:PIN"]


def test_the_redactor_masks_known_values_first_and_records_only_their_names() -> None:
    redactor = Redactor(rules=("api_keys",)).with_known(KnownValues({"env:T": SECRET}))
    assert redactor.text(f"token={SECRET}") == "token=«redacted:env:T»"
    assert redactor.json({"a": [f"x {SECRET}"]}) == {"a": ["x «redacted:env:T»"]}
    config = redactor.to_config()
    assert config.known == ["env:T"] and SECRET not in config.model_dump_json()
    assert Redactor().with_known(KnownValues({})).active is False


def test_masking_is_linear_on_adversarial_text() -> None:
    values = {f"env:V{i}": f"secret-{i:03d}-{'x' * 20}" for i in range(50)}
    text = ("secret-0" * 2000 + "%2f%2F" * 2000 + "&amp;" * 2000) * 2  # ~100 KB of near-misses
    started = time.perf_counter()
    KnownValues(values).text(text)
    assert time.perf_counter() - started < 2.0


def test_a_known_secret_is_masked_even_as_a_json_key() -> None:
    """Rules keep keys (design §18.12); a server echoing a header as a key must not keep it."""
    redactor = Redactor().with_known(KnownValues({"env:T": SECRET}))
    assert redactor.json({SECRET: {"a": 1}, "plain": SECRET}) == {
        "«redacted:env:T»": {"a": 1},
        "plain": "«redacted:env:T»",
    }


def test_markers_are_never_rewritten_by_a_later_secret() -> None:
    """Codex E4d: a short secret (`env`) inside a marker inserted for another, or already in the
    text, must leave the marker whole; redacting twice gives the same text."""
    known = KnownValues({"env:TOKEN": "tok-0123456789", "env:SHORT": "env"})
    once = known.text("tok-0123456789 and env and «redacted:api_keys»")
    assert once == "«redacted:env:TOKEN» and «redacted:env:SHORT» and «redacted:api_keys»"
    assert known.text(once) == once
