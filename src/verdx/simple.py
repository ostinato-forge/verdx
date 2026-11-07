"""Convenience wrappers — SimpleValidator and SimpleFixer.

These let domain gate authors write validators and fixers as plain functions
instead of implementing the full protocols.

    from verdx.simple import simple_validator, simple_fixer

    @simple_validator(id="check-readme", version="1.0.0", category="structure")
    def check_readme(target: Path) -> list[str]:
        issues = []
        if not (target / "README.md").exists():
            issues.append("missing README.md")
        return issues

    @simple_fixer(id="fix-readme", categories={"structure"})
    def fix_readme(target: Path, observation: Observation) -> str | None:
        if "missing README.md" in observation.message:
            (target / "README.md").write_text("# Project\\n")
            return "Created default README.md"
        return None
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from verdx.contracts.dimensions import Confidence, DimLevel, Severity
from verdx.contracts.observation import ObservationDraft
from verdx.contracts.paths import iter_target_files
from verdx.contracts.validator import ValidationContext, ValidatorResult
from verdx.fixers import FixerSpec
from verdx.types import FixAction, Observation

if TYPE_CHECKING:
    from collections.abc import Callable


# ── SimpleValidator ──────────────────────────────────────────────────── #


@dataclass
class SimpleValidatorWrapper:
    """Wraps a plain function into the full Validator protocol."""

    validator_id: str
    validator_version: str
    category: str
    severity: Severity
    confidence: Confidence
    fn: Callable[[Path], list[str]]
    file_suffix: str | None = None

    def __call__(self, context: ValidationContext) -> ValidatorResult:
        start = time.monotonic()
        target = Path(context.target_path)

        try:
            if (
                self.file_suffix is not None
                and next(iter_target_files(target, self.file_suffix), None) is None
            ):
                return ValidatorResult(
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    drafts=[],
                    checks_run=0,
                    checks_passed=0,
                    duration_ms=0,
                    status="skipped",
                    skip_reason=f"no applicable {self.file_suffix} files",
                )
            messages = self.fn(target)
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return ValidatorResult(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                drafts=[],
                checks_run=1,
                checks_passed=0,
                duration_ms=elapsed,
                status="failed",
                error=str(exc),
            )

        drafts: list[ObservationDraft] = []
        for msg in messages:
            drafts.append(
                ObservationDraft(
                    target_key=context.target_key,
                    location=None,
                    rule_id=f"{self.validator_id}:check",
                    category=self.category,
                    severity=self.severity,
                    confidence=self.confidence,
                    dimensions={self.category: DimLevel.MEDIUM},
                    summary=msg,
                )
            )

        elapsed = int((time.monotonic() - start) * 1000)
        return ValidatorResult(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            drafts=drafts,
            checks_run=1,
            checks_passed=0 if drafts else 1,
            duration_ms=elapsed,
            status="completed",
        )


def simple_validator(
    *,
    id: str,
    version: str = "1.0.0",
    category: str,
    severity: str = "medium",
    confidence: str = "confirmed",
    file_suffix: str | None = None,
) -> Callable[[Callable[[Path], list[str]]], SimpleValidatorWrapper]:
    """Decorator that turns a function into a Validator.

    The wrapped function takes a target Path and returns a list of
    issue messages (empty list = clean).
    """

    def decorator(fn: Callable[[Path], list[str]]) -> SimpleValidatorWrapper:
        wrapper = SimpleValidatorWrapper(
            validator_id=id,
            validator_version=version,
            category=category,
            severity=Severity(severity),
            confidence=Confidence(confidence),
            fn=fn,
            file_suffix=file_suffix,
        )
        # Adopt the wrapped function's identity so ``check_from_validator``
        # (which reads ``__module__``/``__qualname__``) builds an import path
        # that resolves back to this wrapper.
        wrapper.__module__ = fn.__module__
        wrapper.__qualname__ = fn.__qualname__  # type: ignore[attr-defined]
        wrapper.__name__ = fn.__name__  # type: ignore[attr-defined]
        return wrapper

    return decorator


# ── SimpleFixer ──────────────────────────────────────────────────────── #


@dataclass
class SimpleFixerWrapper:
    """Wraps a plain function into the Fixer protocol."""

    fixer_id: str
    categories: frozenset[str]
    fn: Callable[[Path, Observation], str | None]
    message_pattern: str | None = None

    def can_fix(self, observation: Observation) -> bool:
        if observation.category not in self.categories:
            return False
        if self.message_pattern is not None:
            return self.message_pattern in observation.message
        return True

    def fix(self, target: Path, observation: Observation) -> FixAction | None:
        description = self.fn(target, observation)
        if description is None:
            return None
        return FixAction(
            observation_signature=observation.signature,
            fixer_id=self.fixer_id,
            description=description,
        )

    def as_spec(self, description: str = "") -> FixerSpec:
        return FixerSpec(
            fixer_id=self.fixer_id,
            description=description or f"SimpleFixer: {self.fixer_id}",
            categories=self.categories,
            fixer=self,
        )


def simple_fixer(
    *,
    id: str,
    categories: set[str],
    message_contains: str | None = None,
) -> Callable[[Callable[[Path, Observation], str | None]], SimpleFixerWrapper]:
    """Decorator that turns a function into a Fixer.

    The wrapped function takes (target_path, observation) and returns a
    description string if fixed, or None if it can't fix this one.

    ``message_contains`` narrows ``can_fix()`` to observations whose message
    includes the given substring, preventing false matches against unrelated
    findings in the same category.
    """

    def decorator(
        fn: Callable[[Path, Observation], str | None],
    ) -> SimpleFixerWrapper:
        wrapper = SimpleFixerWrapper(
            fixer_id=id,
            categories=frozenset(categories),
            fn=fn,
            message_pattern=message_contains,
        )
        wrapper.__module__ = fn.__module__
        wrapper.__qualname__ = fn.__qualname__  # type: ignore[attr-defined]
        wrapper.__name__ = fn.__name__  # type: ignore[attr-defined]
        return wrapper

    return decorator
