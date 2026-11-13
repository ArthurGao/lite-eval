"""Process settings from LITE_EVAL_* environment variables (design §3 L28: no field aliases).

No env file is ever read: CI mode must not depend on a developer's local `.env` (design §6).
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LITE_EVAL_", extra="ignore")

    root: Path | None = None  # evaluation root; default: derived from the suite path
    allow_paid: bool = False  # LITE_EVAL_ALLOW_PAID=1: one of the two conditions of design §18.13


def ci_from_env() -> bool:
    """`CI=true` (or 1 / yes) turns CI mode on, like `--ci`."""
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}
