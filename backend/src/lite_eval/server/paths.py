"""Where the built frontend lives; importable without the `server` extra (the HTML export
needs it too)."""

from pathlib import Path

STATIC = Path(__file__).parent / "static"
NO_FRONTEND = (
    "The Viewer's frontend is not built. From backend/, run `uv run python "
    "scripts/build_frontend.py`; the API under /api works without it."
)
