"""Smoke tests: package imports and CLI prints help."""

from __future__ import annotations

import subprocess
import sys

import verdx.engine


def test_package_imports():
    assert verdx.engine.__version__ == "1.0.0"


def test_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "verdx.engine", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "verdx" in result.stdout


def test_cli_version():
    result = subprocess.run(
        [sys.executable, "-m", "verdx.engine", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert verdx.engine.__version__ in result.stdout


def test_validate_subcommand_exists():
    result = subprocess.run(
        [sys.executable, "-m", "verdx.engine", "validate", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "target" in result.stdout.lower()


def test_quick_subcommand_exists():
    result = subprocess.run(
        [sys.executable, "-m", "verdx.engine", "quick", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "quick checkpoint" in result.stdout.lower()


def test_disposition_subcommand_exists():
    result = subprocess.run(
        [sys.executable, "-m", "verdx.engine", "disposition", "add", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "fingerprint" in result.stdout.lower()
