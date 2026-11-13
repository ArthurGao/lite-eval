"""Starting the Viewer: `lite-eval view` and `lite-eval view --check` (design §10, §18.14).

uvicorn 0.54 is verified for: `Server(Config(app)).run(sockets=[...])`, `Server.started`,
`Server.should_exit`, and skipping signal handlers off the main thread (`capture_signals`).
"""

import secrets
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx2
import uvicorn

from lite_eval.errors import ConfigError
from lite_eval.index import IndexCatalog
from lite_eval.server.app import create_app
from lite_eval.server.guard import is_loopback


def check_host(host: str) -> str:
    """The Viewer listens on loopback only (§18.10); anything else is refused before binding."""
    if not is_loopback(host):
        raise ConfigError(
            f"the Viewer binds loopback only (127.0.0.1, ::1, localhost), not {host!r}"
        )
    return host


def resolve_root(root: Path | None, cwd: Path) -> Path:
    """--root, else the current directory when it holds runs/ or suites/, else ./evals."""
    if root is not None:
        return root
    if (cwd / "runs").is_dir() or (cwd / "suites").is_dir():
        return cwd
    if (cwd / "evals").is_dir():
        return cwd / "evals"
    raise ConfigError(f"no evaluation root in {cwd} (no runs/, suites/ or evals/); pass --root")


def _origins(host: str, port: int) -> set[str]:
    hosts = {host, "127.0.0.1", "localhost"} if host in ("127.0.0.1", "localhost") else {host}
    return {f"http://{h}:{port}" if ":" not in h else f"http://[{h}]:{port}" for h in hosts}


@contextmanager
def _viewer(
    root: Path, host: str, port: int, static_dir: Path
) -> Iterator[tuple[uvicorn.Server, socket.socket]]:
    """A bound socket, the index (and its lock) and a server ready to run; all released after."""
    family = socket.AF_INET6 if ":" in check_host(host) else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise ConfigError(f"cannot listen on {host}:{port}: {exc}") from exc
        bound_port = sock.getsockname()[1]
        catalog = IndexCatalog.open(root)
        try:
            app = create_app(
                root,
                catalog=catalog,
                token=secrets.token_urlsafe(32),
                static_dir=static_dir,
                origins=_origins(host, bound_port),
            )
            yield uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off")), sock
        finally:
            catalog.close()


def serve(root: Path, host: str, port: int, static_dir: Path) -> None:
    """Run the Viewer until interrupted."""
    with _viewer(root, host, port, static_dir) as (server, sock):
        server.run(sockets=[sock])


def check(root: Path | None, static_dir: Path) -> dict[str, int]:
    """Start on a free loopback port, GET / and /api/health, stop. Without --root it uses an
    empty temporary root, so a check never takes another Viewer's lock."""
    with (
        tempfile.TemporaryDirectory() as empty,
        _viewer(root or Path(empty), "127.0.0.1", 0, static_dir) as (server, sock),
    ):
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline or not thread.is_alive():
                    raise ConfigError("the Viewer did not start within 10 s")
                time.sleep(0.02)
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
            with httpx2.Client(timeout=10) as client:
                return {p: client.get(base + p).status_code for p in ("/", "/api/health")}
        finally:
            server.should_exit = True
            thread.join(10)
