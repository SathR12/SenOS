from typer.testing import CliRunner

from senos.cli import app

runner = CliRunner()


def test_version_prints_package_version():
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


def test_restore_without_recorded_changes_is_honest_noop(tmp_path):
    result = runner.invoke(app, ["restore", "--db", str(tmp_path / "missing.sqlite3")])
    assert result.exit_code == 0
    assert "No recorded changes" in result.output
