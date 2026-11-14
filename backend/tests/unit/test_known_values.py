"""Known secrets masked by value, in every form a reply may repeat them (design §18.12, E4d)."""

import html
import json
import time
import urllib.parse
from pathlib import Path

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


GO_JSON = 'sk/a"b<c>d&e é-0123456789'  # what other encoders escape differently


@pytest.mark.parametrize(
    "form",
    [
        "sk\\u002fa\\u0022b\\u003cc\\u003ed\\u0026e \\u00e9-0123456789",  # every special as \\uXXXX
        'sk\\u002Fa\\"b\\u003Cc>d&e é-0123456789',  # mixed: some escaped, some not, upper hex
        "sk&#47;a&quot;b&lt;c&#x3e;d&#38;e &#233;-0123456789",  # HTML numeric and named entities
        "sk&#x2F;a&#34;b&#X3C;c&gt;d&amp;e é-0123456789",
    ],
)
def test_each_encoder_s_escapes_of_a_value_are_masked_in_any_mix(form: str) -> None:
    """Security review of 9d8e815: Go's encoding/json writes `<` as `\\u003c`, others `/` as
    `\\u002f`, HTML may use numeric entities; a reply may escape some characters only."""
    known = KnownValues({"env:TOKEN": GO_JSON})
    assert known.text(f"[{form}]") == "[«redacted:env:TOKEN»]"


def test_a_character_outside_the_bmp_is_masked_as_a_surrogate_pair_escape() -> None:
    secret = "key-\U0001f511-0123456789"
    known = KnownValues({"env:TOKEN": secret})
    assert known.text(json.dumps(secret)) == '"«redacted:env:TOKEN»"'  # \\ud83d\\udd11
    upper = "key-\\uD83D\\uDD11-0123456789"  # another encoder's case
    assert known.text(upper) == "«redacted:env:TOKEN»"


def test_stderr_kept_up_to_its_cap_drops_a_possible_secret_start_at_the_cut() -> None:
    """Also when a chunk ended exactly at the cap and the secret goes on in the next one."""
    import lite_eval.targets.command as command
    from lite_eval.targets.command import CommandSpec, CommandTarget

    target = CommandTarget(CommandSpec(argv=["true"]), {}, Path("."))
    target._known = KnownValues({"env:T": "tok-0123456789"})
    cap = command.STDERR_CAP
    target._keep(b"x" * (cap - 7) + b"tok-012")  # exactly to the cap: nothing dropped yet
    target._keep(b"3456789 and more")
    assert b"tok-" not in target._stderr
    assert len(target._stderr) + target._stderr_dropped == cap - 7 + 7 + len(b"3456789 and more")


def test_once_stderr_is_cut_nothing_more_is_kept() -> None:
    """Security review of 412af4b: the trim made room again, and a later chunk was kept after
    the gap (and could be cut inside a secret with no trim)."""
    import lite_eval.targets.command as command
    from lite_eval.targets.command import CommandSpec, CommandTarget

    target = CommandTarget(CommandSpec(argv=["true"]), {}, Path("."))
    target._known = KnownValues({"env:T": "tok-0123456789"})
    target._keep(b"x" * (command.STDERR_CAP + 10))
    kept = bytes(target._stderr)
    target._keep(b"tok-0123")
    assert bytes(target._stderr) == kept
    assert len(kept) + target._stderr_dropped == command.STDERR_CAP + 10 + len(b"tok-0123")


def test_entities_with_leading_zeros_are_masked_within_the_bound_used_to_cut() -> None:
    """Security review of 412af4b: unbounded leading zeros made `longest()` no bound."""
    secret = "/x0123456789"
    known = KnownValues({"env:T": secret})
    longest_form = "&#x" + "0" * 8 + "2f;x0123456789"
    assert known.text(longest_form) == "«redacted:env:T»"
    assert known.text("&#" + "0" * 8 + "47;x0123456789") == "«redacted:env:T»"
    assert known.longest() >= len(longest_form.encode())
    assert known.longest() >= len(("&#" + "0" * 8 + "1114111;").encode()) * len(secret)
