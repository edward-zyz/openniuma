# SPDX-License-Identifier: MIT
"""CLI 命令测试。"""

from click.testing import CliRunner

from openniuma.cli import main


def test_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "openNiuMa" in result.output


def test_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_unknown_command():
    runner = CliRunner()
    result = runner.invoke(main, ["nonexistent"])
    assert result.exit_code != 0


def test_doctor_runs():
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "Python" in result.output
    assert "Git" in result.output
