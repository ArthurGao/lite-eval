"""Build the Viewer and copy it into the package (design §18.14).

Runs `pnpm install --frozen-lockfile` and `pnpm build` in ../frontend, then replaces
src/lite_eval/server/static/ with frontend/dist/. hatchling's `artifacts` setting ships that
gitignored directory in the wheel and the sdist, so `lite-eval view` needs no Node.
`--skip-install` reuses the installed node_modules (local iteration).
"""

import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
STATIC = BACKEND / "src" / "lite_eval" / "server" / "static"


def main(argv: list[str]) -> int:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print(
            "pnpm is not on PATH; install Node 22 and pnpm 10 (frontend/package.json)",
            file=sys.stderr,
        )
        return 2
    if "--skip-install" not in argv:
        subprocess.run([pnpm, "install", "--frozen-lockfile"], cwd=FRONTEND, check=True)
    subprocess.run([pnpm, "build"], cwd=FRONTEND, check=True)
    dist = FRONTEND / "dist"
    if not (dist / "index.html").is_file():
        print(f"no index.html in {dist}; the build did not produce the Viewer", file=sys.stderr)
        return 3
    if STATIC.exists():
        shutil.rmtree(STATIC)
    shutil.copytree(dist, STATIC)
    print(f"copied {dist} -> {STATIC}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
