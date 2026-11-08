from typer.testing import CliRunner

from senos.cli import app

runner = CliRunner()


def test_version_prints_package_version():
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


def test_restore_fails_until_implemented():
    result = runner.invoke(app, ["restore"])

    assert result.exit_code == 1
    assert "not implemented yet" in result.output
