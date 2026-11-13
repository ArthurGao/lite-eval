"""The built distributions carry what a public Apache-2.0 package must (design §18.14)."""

import subprocess
import tarfile
import zipfile
from pathlib import Path

BACKEND = Path(__file__).parents[2]


def test_wheel_and_sdist_include_the_license_text(tmp_path: Path) -> None:
    # hatchling only picks up license files inside backend/, so backend/LICENSE copies the root one.
    assert (BACKEND / "LICENSE").read_bytes() == (BACKEND.parent / "LICENSE").read_bytes()
    subprocess.run(
        ["uv", "build", "--quiet", "--out-dir", str(tmp_path)],
        cwd=BACKEND,
        check=True,
        timeout=120,
    )
    wheel = next(tmp_path.glob("lite_eval-*.whl"))
    sdist = next(tmp_path.glob("lite_eval-*.tar.gz"))

    dist_info = f"{wheel.name.removesuffix('-py3-none-any.whl')}.dist-info"
    with zipfile.ZipFile(wheel) as zf:
        assert f"{dist_info}/licenses/LICENSE" in zf.namelist()
        assert "Apache License" in zf.read(f"{dist_info}/licenses/LICENSE").decode()
        assert "License-Expression: Apache-2.0" in zf.read(f"{dist_info}/METADATA").decode()
    with tarfile.open(sdist) as tf:
        assert f"{sdist.name.removesuffix('.tar.gz')}/LICENSE" in tf.getnames()
