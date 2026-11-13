"""The output cache (design §18.5). Entries are redacted Outputs; a corrupt entry is a miss."""

from pathlib import Path
from typing import Any

from lite_eval.canonical import content_hash
from lite_eval.contracts import Output
from lite_eval.files import atomic_write


def cache_key(
    *,
    target: str,
    config: dict[str, Any],
    versions: dict[str, str],
    setup: dict[str, Any],
    dataset_hash: str,
    case_hash: str,
    repeat: int,
) -> str:
    return content_hash(
        {
            "target": target,
            "config": config,
            "versions": versions,
            "setup": setup,
            "dataset": dataset_hash,
            "case": case_hash,
            "repeat": repeat,
        }
    )


class OutputCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> tuple[Output | None, bool]:
        """(output, corrupt): a missing entry is (None, False), an unreadable one (None, True)."""
        try:
            return Output.model_validate_json(self._path(key).read_bytes()), False
        except FileNotFoundError:
            return None, False
        except (OSError, ValueError):  # pydantic's ValidationError is a ValueError
            return None, True

    def put(self, key: str, output: Output) -> None:
        atomic_write(self._path(key), output.model_dump_json(), durable=False)
