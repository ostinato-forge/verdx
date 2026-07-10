"""Tests for profile definitions and threshold resolution."""

from __future__ import annotations

import pytest

from verdx.profiles import DEFAULT_PROFILE, PROFILES, resolve_threshold


def test_profiles_defined() -> None:
    assert set(PROFILES) == {"quick", "standard", "strict"}


def test_quick_threshold() -> None:
    assert resolve_threshold("quick") == 60


def test_standard_threshold() -> None:
    assert resolve_threshold("standard") == 80


def test_strict_threshold() -> None:
    assert resolve_threshold("strict") == 95


def test_default_is_standard() -> None:
    assert DEFAULT_PROFILE == "standard"


def test_unknown_profile() -> None:
    with pytest.raises(ValueError, match="unknown profile"):
        resolve_threshold("paranoid")
