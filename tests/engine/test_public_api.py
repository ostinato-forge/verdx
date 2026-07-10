"""Verify verdx.engine.api matches the platform contract surface."""

from __future__ import annotations

import verdx.engine.api as api


class TestPlatformContractSymbols:
    """Every symbol in platform-contract.md must be importable from verdx.engine.api."""

    def test_validate_exists(self) -> None:
        assert callable(api.validate)

    def test_quick_check_exists(self) -> None:
        assert callable(api.quick_check)

    def test_run_assessment_importable(self) -> None:
        assert api.RunAssessment is not None

    def test_observation_importable(self) -> None:
        assert api.Observation is not None

    def test_score_importable(self) -> None:
        assert api.Score is not None

    def test_list_domains_importable(self) -> None:
        assert callable(api.list_domains)

    def test_get_domain_importable(self) -> None:
        assert callable(api.get_domain)

    def test_detect_domains_importable(self) -> None:
        assert callable(api.detect_domains)

    def test_all_exports_exist(self) -> None:
        for name in api.__all__:
            assert hasattr(api, name), f"missing export: {name}"

    def test_quick_check_is_callable(self) -> None:
        assert callable(api.quick_check)


class TestValidateFunction:
    def test_no_domains_detected_raises(self, tmp_path: object) -> None:
        from pathlib import Path

        import pytest

        empty = Path(str(tmp_path))
        with pytest.raises((ValueError, Exception)):
            api.validate(empty)
