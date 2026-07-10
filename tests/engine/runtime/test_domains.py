"""Tests for validation domain registry with entry-point discovery.

These tests verify:
- Entry-point based domain discovery
- Domain auto-detection from markers
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
from verdx.engine.runtime.domains import (
    UnknownDomainError,
    _reset_discovery_cache,
    detect_domain,
    detect_domains,
    get_domain,
    list_domains,
)


def _fake_spec(name: str = "fake", markers: tuple[str, ...] = ()) -> DomainSpec:
    return DomainSpec(
        name=name,
        markers=markers,
        load_default_config=lambda: None,  # type: ignore[arg-type]
        load_checks=lambda: (),
        load_profiles=lambda: (),
        discover_subjects=lambda _p, _k: SubjectDiscoveryResult(),
    )


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    """Reset the module-level discovery cache before and after each test."""
    _reset_discovery_cache()
    yield
    _reset_discovery_cache()


# ===========================================================================
# Entry-point discovery
# ===========================================================================


class TestDiscovery:
    def test_discovers_from_entry_points(self) -> None:
        """Entry points that return valid DomainSpecs are registered."""
        mock_ep = MagicMock()
        mock_ep.name = "test-domain"
        mock_ep.load.return_value = lambda: _fake_spec("test-domain")

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[mock_ep]):
            assert "test-domain" in list_domains()

    def test_skips_broken_entry_points(self) -> None:
        """Entry points that raise are logged and skipped."""
        broken_ep = MagicMock()
        broken_ep.name = "broken"
        broken_ep.load.side_effect = ImportError("missing dep")

        good_ep = MagicMock()
        good_ep.name = "good"
        good_ep.load.return_value = lambda: _fake_spec("good")

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[broken_ep, good_ep]):
            domains = list_domains()
        assert "good" in domains
        assert "broken" not in domains

    def test_duplicate_success_clears_same_name_load_error(self) -> None:
        """A working provider wins over a failed duplicate with the same name."""
        broken_ep = MagicMock()
        broken_ep.name = "training"
        broken_ep.load.side_effect = ImportError("old provider missing")

        good_ep = MagicMock()
        good_ep.name = "training"
        good_ep.load.return_value = lambda: _fake_spec("training")

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[broken_ep, good_ep]):
            assert "training" in list_domains()
            from verdx.engine.runtime.domains import domain_load_errors

            assert "training" not in domain_load_errors()

    def test_skips_non_domainspec_return(self) -> None:
        """Entry points that return non-DomainSpec are skipped."""
        bad_ep = MagicMock()
        bad_ep.name = "bad"
        bad_ep.load.return_value = lambda: {"not": "a DomainSpec"}

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[bad_ep]):
            assert list_domains() == ()

    def test_discovery_is_cached(self) -> None:
        """Second call returns the same result without calling entry_points again."""
        mock_ep = MagicMock()
        mock_ep.name = "cached"
        mock_ep.load.return_value = lambda: _fake_spec("cached")

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[mock_ep]) as ep_mock:
            list_domains()
            list_domains()
        ep_mock.assert_called_once()

    def test_no_domains_installed(self) -> None:
        """When no entry points are found, list_domains returns empty tuple."""
        with patch("verdx.engine.runtime.domains.entry_points", return_value=[]):
            assert list_domains() == ()

    def test_unknown_domain_error_lists_known(self) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "known"
        mock_ep.load.return_value = lambda: _fake_spec("known")

        with (
            patch("verdx.engine.runtime.domains.entry_points", return_value=[mock_ep]),
            pytest.raises(UnknownDomainError, match="known domains: known"),
        ):
            get_domain("nope")

    def test_unknown_domain_error_lists_example_domain(self) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "example"
        mock_ep.load.return_value = lambda: _fake_spec("example")

        with (
            patch("verdx.engine.runtime.domains.entry_points", return_value=[mock_ep]),
            pytest.raises(UnknownDomainError, match=r"known domains: .*example"),
        ):
            get_domain("nope")


# ===========================================================================
# Domain auto-detection
# ===========================================================================


class TestDetectDomain:
    def test_detects_from_marker_file(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("course: test")

        mock_ep = MagicMock()
        mock_ep.name = "training"
        mock_ep.load.return_value = lambda: _fake_spec("training", markers=("outline.yml",))

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[mock_ep]):
            result = detect_domain(tmp_path)

        assert result == "training"

    def test_returns_none_for_empty_directory(self, tmp_path: Path) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "training"
        mock_ep.load.return_value = lambda: _fake_spec("training", markers=("outline.yml",))

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[mock_ep]):
            result = detect_domain(tmp_path)
        assert result is None

    def test_returns_none_for_no_domains(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("course: test")

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[]):
            result = detect_domain(tmp_path)
        assert result is None

    def test_detects_from_file_path_parent(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("course: test")
        target_file = tmp_path / "somefile.adoc"
        target_file.write_text("= Title")

        mock_ep = MagicMock()
        mock_ep.name = "training"
        mock_ep.load.return_value = lambda: _fake_spec("training", markers=("outline.yml",))

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[mock_ep]):
            result = detect_domain(target_file)
        assert result == "training"

    def test_multiple_domains_sorted_order(self, tmp_path: Path) -> None:
        """When multiple domains match, first in sorted-name order wins."""
        (tmp_path / "marker.txt").write_text("")

        ep_a = MagicMock()
        ep_a.name = "beta"
        ep_a.load.return_value = lambda: _fake_spec("beta", markers=("marker.txt",))

        ep_b = MagicMock()
        ep_b.name = "alpha"
        ep_b.load.return_value = lambda: _fake_spec("alpha", markers=("marker.txt",))

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[ep_a, ep_b]):
            result = detect_domain(tmp_path)
        assert result == "alpha"


# ===========================================================================
# Multi-domain auto-detection
# ===========================================================================


class TestDetectDomains:
    def test_returns_all_matching(self, tmp_path: Path) -> None:
        (tmp_path / "marker.txt").write_text("")

        ep_a = MagicMock()
        ep_a.name = "alpha"
        ep_a.load.return_value = lambda: _fake_spec("alpha", markers=("marker.txt",))

        ep_b = MagicMock()
        ep_b.name = "beta"
        ep_b.load.return_value = lambda: _fake_spec("beta", markers=("marker.txt",))

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[ep_a, ep_b]):
            result = detect_domains(tmp_path)
        assert result == ("alpha", "beta")

    def test_sorted_order(self, tmp_path: Path) -> None:
        (tmp_path / "m.txt").write_text("")

        ep_z = MagicMock()
        ep_z.name = "zulu"
        ep_z.load.return_value = lambda: _fake_spec("zulu", markers=("m.txt",))

        ep_a = MagicMock()
        ep_a.name = "alpha"
        ep_a.load.return_value = lambda: _fake_spec("alpha", markers=("m.txt",))

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[ep_z, ep_a]):
            result = detect_domains(tmp_path)
        assert result == ("alpha", "zulu")

    def test_empty_on_no_match(self, tmp_path: Path) -> None:
        ep = MagicMock()
        ep.name = "d"
        ep.load.return_value = lambda: _fake_spec("d", markers=("nope.yml",))

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[ep]):
            result = detect_domains(tmp_path)
        assert result == ()

    def test_partial_match(self, tmp_path: Path) -> None:
        (tmp_path / "outline.yml").write_text("")

        ep_match = MagicMock()
        ep_match.name = "training"
        ep_match.load.return_value = lambda: _fake_spec("training", markers=("outline.yml",))

        ep_no = MagicMock()
        ep_no.name = "editing"
        ep_no.load.return_value = lambda: _fake_spec("editing", markers=("style.yml",))

        with patch(
            "verdx.engine.runtime.domains.entry_points",
            return_value=[ep_match, ep_no],
        ):
            result = detect_domains(tmp_path)
        assert result == ("training",)

    def test_multiple_markers_per_domain(self, tmp_path: Path) -> None:
        (tmp_path / "second.yml").write_text("")

        ep = MagicMock()
        ep.name = "multi"
        ep.load.return_value = lambda: _fake_spec("multi", markers=("first.yml", "second.yml"))

        with patch("verdx.engine.runtime.domains.entry_points", return_value=[ep]):
            result = detect_domains(tmp_path)
        assert result == ("multi",)
