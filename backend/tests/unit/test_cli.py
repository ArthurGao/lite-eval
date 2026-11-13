from importlib.metadata import version

from typer.testing import CliRunner

from lite_eval.cli import app

runner = CliRunner()


def test_help_exits_zero_and_names_the_tool() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Usage: lite-eval" in result.output
    assert (
        "lite-eval: did this change make the system better or worse, and can we trust that?"
        in result.output
    )


def test_version_prints_the_installed_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"lite-eval {version('lite-eval')}"
