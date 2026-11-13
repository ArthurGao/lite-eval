"""Python targets: `python: module:factory` in a suite, one call per config (design §18.20)."""

import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from lite_eval.errors import ConfigError
from lite_eval.targets.base import Target


def load_factory(spec: str, import_root: Path) -> Callable[..., Any]:
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise ConfigError(f"python target must be 'module:factory', got {spec!r}")
    root = str(import_root)
    added = root not in sys.path
    if added:
        sys.path.insert(0, root)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(f"cannot import python target module {module_name!r}: {exc}") from exc
    finally:
        if added:
            sys.path.remove(root)
    factory = getattr(module, attr, None)
    if not callable(factory):
        raise ConfigError(f"python target {spec!r}: {attr!r} is not a callable in {module_name}")
    return cast(Callable[..., Any], factory)


def build_target(factory: Callable[..., Any], params: dict[str, Any], spec: str) -> Target:
    try:
        target = factory(**params)
    except TypeError as exc:
        raise ConfigError(f"python target {spec!r} rejected config {params}: {exc}") from exc
    if not isinstance(target, Target):
        raise ConfigError(f"python target {spec!r} returned {type(target).__name__}, not a Target")
    return target
