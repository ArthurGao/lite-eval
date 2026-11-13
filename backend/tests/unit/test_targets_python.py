import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from lite_eval.errors import ConfigError
from lite_eval.targets.fake import FakeTarget
from lite_eval.targets.python import build_target, load_factory


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    (tmp_path / "my_targets.py").write_text(
        "from lite_eval.targets.fake import FakeTarget\n"
        "def make(**kw): return FakeTarget(config=kw)\n"
        "def not_a_target(**kw): return 42\n"
        "NOT_CALLABLE = 1\n"
    )
    yield tmp_path
    sys.modules.pop("my_targets", None)


def test_a_factory_is_imported_from_the_repo_and_called_with_the_config(repo: Path) -> None:
    factory = load_factory("my_targets:make", repo)
    target = build_target(factory, {"mode": "hybrid", "k": 5}, "my_targets:make")

    assert isinstance(target, FakeTarget)
    assert target.config() == {"mode": "hybrid", "k": 5}
    assert str(repo) not in sys.path  # only on sys.path while importing


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("my_targets", "must be 'module:factory'"),
        ("missing_module:make", "cannot import"),
        ("my_targets:NOT_CALLABLE", "is not a callable"),
        ("my_targets:absent", "is not a callable"),
    ],
)
def test_bad_specs_are_config_errors(repo: Path, spec: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_factory(spec, repo)


def test_a_factory_that_returns_something_else_is_a_config_error(repo: Path) -> None:
    factory = load_factory("my_targets:not_a_target", repo)

    with pytest.raises(ConfigError, match="returned int, not a Target"):
        build_target(factory, {}, "my_targets:not_a_target")


def test_a_config_the_factory_does_not_accept_is_a_config_error() -> None:
    def make(mode: str) -> FakeTarget:
        return FakeTarget()

    with pytest.raises(ConfigError, match="rejected config"):
        build_target(make, {"colour": "red"}, "x:make")
