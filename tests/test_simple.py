"""Tests for SimpleValidator and SimpleFixer convenience wrappers."""

from __future__ import annotations

from pathlib import Path

import pytest

from verdx.contracts.check import check_from_validator
from verdx.contracts.validator import ValidationContext
from verdx.simple import simple_fixer, simple_validator
from verdx.types import Observation


@simple_validator(id="foo-check", category="structure")
def foo(target: Path) -> list[str]:
    return []


class TestValidatorIdentity:
    def test_wrapper_adopts_wrapped_function_identity(self) -> None:
        assert foo.__qualname__ == "foo"
        assert foo.__name__ == "foo"
        assert foo.__module__ == __name__

    def test_check_from_validator_import_path_resolves(self) -> None:
        spec = check_from_validator(foo, {}, domain="d")
        assert spec.adapter.import_path == f"{__name__}:foo"

    def test_nested_validator_rejected_early(self) -> None:
        # A validator defined inside a function is not importable by qualname:
        # its __qualname__ contains "<locals>". check_from_validator must reject
        # it up front with a clear authoring error naming the validator, not
        # build an import path that only fails later during execution.
        @simple_validator(id="nested-check", category="structure")
        def nested(target: Path) -> list[str]:
            return []

        with pytest.raises(ValueError, match="nested-check"):
            check_from_validator(nested, {}, domain="d")


class TestSimpleValidator:
    def _context(self, target: Path) -> ValidationContext:
        return ValidationContext(
            run_id="test-run",
            target_key="test",
            target_path=str(target),
            target_content_hash="sha256:abc",
            config={},
            previous_observations=[],
            timeout_seconds=30,
        )

    def test_clean_target(self, tmp_path: Path) -> None:
        @simple_validator(id="test-v", category="structure")
        def check(target: Path) -> list[str]:
            return []

        result = check(self._context(tmp_path))
        assert result.status == "completed"
        assert result.checks_passed == 1
        assert result.drafts == []

    def test_finding_produces_draft(self, tmp_path: Path) -> None:
        @simple_validator(id="test-v", category="structure")
        def check(target: Path) -> list[str]:
            return ["missing outline.yml"]

        result = check(self._context(tmp_path))
        assert result.status == "completed"
        assert result.checks_passed == 0
        assert len(result.drafts) == 1
        assert result.drafts[0].summary == "missing outline.yml"
        assert result.drafts[0].category == "structure"

    def test_multiple_findings(self, tmp_path: Path) -> None:
        @simple_validator(id="test-v", category="style")
        def check(target: Path) -> list[str]:
            return ["issue 1", "issue 2"]

        result = check(self._context(tmp_path))
        assert len(result.drafts) == 2

    def test_exception_produces_failed(self, tmp_path: Path) -> None:
        @simple_validator(id="test-v", category="structure")
        def check(target: Path) -> list[str]:
            raise RuntimeError("boom")

        result = check(self._context(tmp_path))
        assert result.status == "failed"
        assert result.error == "boom"

    def test_custom_severity(self, tmp_path: Path) -> None:
        @simple_validator(id="test-v", category="structure", severity="critical")
        def check(target: Path) -> list[str]:
            return ["critical issue"]

        result = check(self._context(tmp_path))
        assert result.drafts[0].severity.value == "critical"

    def test_validator_id_preserved(self, tmp_path: Path) -> None:
        @simple_validator(id="my-custom-validator", version="2.0.0", category="test")
        def check(target: Path) -> list[str]:
            return []

        result = check(self._context(tmp_path))
        assert result.validator_id == "my-custom-validator"
        assert result.validator_version == "2.0.0"


class TestSimpleFixer:
    def _obs(self, category: str = "structure", message: str = "problem") -> Observation:
        return Observation(
            severity="high",
            category=category,
            location=None,
            message=message,
            fixable=False,
            signature="sig1",
        )

    def test_can_fix_by_category(self) -> None:
        @simple_fixer(id="fix-1", categories={"structure"})
        def fixer(target: Path, obs: Observation) -> str | None:
            return "fixed"

        assert fixer.can_fix(self._obs(category="structure"))
        assert not fixer.can_fix(self._obs(category="style"))

    def test_fix_returns_action(self, tmp_path: Path) -> None:
        @simple_fixer(id="fix-1", categories={"structure"})
        def fixer(target: Path, obs: Observation) -> str | None:
            return "Created missing file"

        action = fixer.fix(tmp_path, self._obs())
        assert action is not None
        assert action.fixer_id == "fix-1"
        assert action.description == "Created missing file"

    def test_fix_returns_none_when_cant_fix(self, tmp_path: Path) -> None:
        @simple_fixer(id="fix-1", categories={"structure"})
        def fixer(target: Path, obs: Observation) -> str | None:
            return None

        assert fixer.fix(tmp_path, self._obs()) is None

    def test_as_spec(self) -> None:
        @simple_fixer(id="fix-1", categories={"structure"})
        def fixer(target: Path, obs: Observation) -> str | None:
            return "fixed"

        spec = fixer.as_spec("My fixer")
        assert spec.fixer_id == "fix-1"
        assert spec.description == "My fixer"
        assert spec.categories == frozenset({"structure"})
