import pytest

from lite_eval.errors import ConfigError
from lite_eval.redact import Redactor, from_spec
from lite_eval.suite import RedactRule

CONN = Redactor(rules=("connection_strings",))
KEYS = Redactor(rules=("api_keys",))
MAILS = Redactor(rules=("emails",))
R = "«redacted:connection_strings»"
K = "«redacted:api_keys»"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # URL: password goes, user / host / port / database / other parameters stay readable.
        (
            "postgresql://app:s3cr%40t@db.local:5432/evals?sslmode=require",
            f"postgresql://app:{R}@db.local:5432/evals?sslmode=require",
        ),
        # A password-like query parameter, any prefix; the next parameter stays.
        (
            "postgres://db/x?sslpassword=p&w0rd&sslmode=verify-full",
            f"postgres://db/x?sslpassword={R}&w0rd&sslmode=verify-full",
        ),
        # libpq key=value: quoted with an escaped quote, bare with '&', any prefix.
        (
            "host=db user=app password='a b\\'c' dbname=x",
            f"host=db user=app password={R} dbname=x",
        ),
        ("PGPASSWORD=pa&ss psql -h db", f"PGPASSWORD={R} psql -h db"),
        ('db_password = "x y" ok', f"db_password = {R} ok"),
        # Unencoded '@' and '/' inside the password: the whole password goes, up to the host.
        ("postgresql://app:p@ss@db:5432/x", f"postgresql://app:{R}@db:5432/x"),
        ("postgresql://app:a/b@db/x ok", f"postgresql://app:{R}@db/x ok"),
        ("redis://:pw@cache", f"redis://:{R}@cache"),
        # No password: unchanged; a port is not a password.
        ("postgresql://app@db/x", "postgresql://app@db/x"),
        ("https://example.com:8080/path?q=1", "https://example.com:8080/path?q=1"),
    ],
)
def test_connection_strings(text: str, expected: str) -> None:
    assert CONN.text(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("key sk-abcdefghijklmnopqrstuv end", f"key {K} end"),
        ("stw_ABCDEFGHIJKLMNOPQRSTUV", K),
        ("aws AKIAABCDEFGHIJKLMNOP id", f"aws {K} id"),
        ("Authorization: Bearer eyJhbGciOi.payload.sig", f"Authorization: Bearer {K}"),
        ("api_key=abc123, next", f"api_key={K}, next"),
        ('{"token": "abc"}', f'{{"token": {K}}}'),
        ("SECRET: hunter2", f"SECRET: {K}"),
        # Word boundaries: usage fields are not secrets.
        ("max_tokens: 4000, input_tokens=12", "max_tokens: 4000, input_tokens=12"),
    ],
)
def test_api_keys(text: str, expected: str) -> None:
    assert KEYS.text(text) == expected


def test_emails_keep_the_surrounding_text() -> None:
    assert MAILS.text("mail jane.doe+x@example.co.nz now") == "mail «redacted:emails» now"


def test_json_values_are_redacted_recursively_and_secret_named_keys_lose_their_value() -> None:
    redactor = Redactor(rules=("api_keys", "emails"))
    value = {
        "answer": "contact a@b.io",
        "headers": {"x-api-key": "plain-looking", "access_token": "t", "max_tokens": "4000"},
        "list": ["sk-abcdefghijklmnopqrstuv", 3, None],
    }

    assert redactor.json(value) == {
        "answer": "contact «redacted:emails»",
        "headers": {"x-api-key": K, "access_token": K, "max_tokens": "4000"},
        "list": [K, 3, None],
    }


def test_redacting_twice_is_stable() -> None:
    redactor = Redactor(rules=("connection_strings", "api_keys", "emails"))
    text = "postgresql://u:p@h/db token=abc Bearer abcdefghijkl a@b.io password='x'"
    once = redactor.text(text)

    assert redactor.text(once) == once
    assert redactor.json(redactor.json({"token": "x"})) == {"token": K}


def test_custom_patterns_replace_the_whole_match() -> None:
    redactor = from_spec(["emails", RedactRule(name="ticket", pattern=r"TCK-\d+")])

    assert (
        redactor.text("see TCK-1234 from a@b.io") == "see «redacted:ticket» from «redacted:emails»"
    )


@pytest.mark.parametrize(
    "spec",
    [
        ["nope"],
        [{"name": "emails", "pattern": "x"}],
        [{"name": "bad", "pattern": "("}],
    ],
)
def test_invalid_rules_are_config_errors(spec: list[str | dict[str, str]]) -> None:
    with pytest.raises(ConfigError):
        from_spec(RedactRule(**i) if isinstance(i, dict) else i for i in spec)


def test_no_rules_changes_nothing() -> None:
    assert (
        Redactor().text("password=x sk-abcdefghijklmnopqrstuv")
        == "password=x sk-abcdefghijklmnopqrstuv"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{token=abc}", f"{{token={K}}}"),
        ("[api_key: abc]", f"[api_key: {K}]"),
        ("(secret=abc) next", f"(secret={K}) next"),
        ('token="a b c" tail', f"token={K} tail"),
        ('token="" tail', f"token={K} tail"),
    ],
)
def test_api_key_values_stop_at_closing_delimiters(text: str, expected: str) -> None:
    assert KEYS.text(text) == expected


ADVERSARIAL = {
    # Each starts a match attempt at many positions; an unbounded run makes that quadratic.
    "many url starts": "a://b:" * 17_000,
    "dotted run": "a." * 50_000,
    "hyphenated run": "a-" * 50_000,
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_redaction_stays_linear_on_adversarial_text(name: str) -> None:
    import time

    redactor = Redactor(rules=("connection_strings", "api_keys", "emails"))
    started = time.perf_counter()
    redactor.text(ADVERSARIAL[name])

    # ~100 KB: linear takes milliseconds; the quadratic patterns took 10 s or more.
    assert time.perf_counter() - started < 2.0
