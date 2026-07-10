"""Public API — the two verdx operations: validate and fix."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from verdx._changed import get_changed_files
from verdx.overrides import load_overrides
from verdx.profiles import resolve_threshold
from verdx.translate import translate_assessment
from verdx.types import Assessment, DriveResult, FixResult

if TYPE_CHECKING:
    from verdx.contracts.disposition import Disposition
    from verdx.types import Override

_log = logging.getLogger(__name__)


_SAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _resolve_target_key(target: Path) -> str:
    """Derive a filesystem-safe flywheel target key from the target path.

    The key scopes flywheel data (patterns, corrections, overrides) under the
    data root, so it becomes part of a filesystem path. It must therefore be
    non-empty and contain no path separators or ``..`` — otherwise a target
    named ``..`` or with a separator in its basename could escape the data root.

    The key combines a sanitized basename (for human readability) with a short
    hash of the target's resolved absolute path (for collision-freedom, so two
    distinct targets that share a basename like ``docs`` get distinct keys).
    """
    resolved = target.resolve()
    raw_name = resolved.name
    # Sanitize: replace any non-portable char, strip leading dots/dashes that
    # would produce hidden or option-like directory names, and collapse to a
    # safe token. Anything that reduces to empty (e.g. "..", "/") falls back to
    # a stable placeholder so the key is always non-empty and separator-free.
    sanitized = _SAFE_KEY_CHARS.sub("-", raw_name).strip("-.")
    if not sanitized or sanitized == "..":
        sanitized = "target"

    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"{sanitized}-{digest}"


def validate(
    target: str | Path,
    *,
    changed_only: bool = False,
    live: bool = True,
    profile: str = "standard",
    domain: str | None = None,
    flywheel: bool = True,
) -> Assessment:
    """Validate a target and return a porcelain Assessment.

    This is the primary entry point for skills calling verdx.
    """
    from verdx.engine.api import validate as engine_validate
    from verdx.fixers import discover_fixers

    target_path = Path(target).resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"target does not exist: {target_path}")

    target_key = _resolve_target_key(target_path)

    threshold = resolve_threshold(profile)
    overrides = load_overrides(target_key)
    active_dispositions = _overrides_to_dispositions(overrides)
    fixers = discover_fixers()

    changed_files: frozenset[str] | None = None
    if changed_only:
        changed_files = get_changed_files(target_path)

    cli_overrides: dict[str, object] = {}

    engine_profile = profile if profile in ("quick", "migration") else None
    engine_result = engine_validate(
        target_path,
        domains=[domain] if domain else None,
        profile=engine_profile,
        static_only=not live,
        threshold=threshold,
        changed_files=changed_files,
        cli_overrides=cli_overrides or None,
        active_dispositions=active_dispositions or None,
    )

    assessment = translate_assessment(
        engine_result,
        threshold=threshold,
        profile=profile,
        fixers=fixers,
    )

    # Suppression now happens inside the engine. Report which overrides actually
    # matched a finding (the engine attached a disposition to it) so the flywheel
    # still learns which corrections were used.
    applied = tuple(
        ov
        for ov in overrides
        if any(
            eo.issue_fingerprint == ov.signature
            and eo.disposition is not None
            and eo.disposition.decision == ov.decision
            for eo in engine_result.observations
        )
    )
    if applied:
        from dataclasses import replace

        assessment = replace(assessment, overrides_applied=applied)

    use_flywheel = flywheel and os.environ.get("VERDX_FLYWHEEL", "1") != "0"
    if use_flywheel:
        assessment = _enrich_with_flywheel(assessment, target_key, domain)
        if assessment.observations:
            _log_validation_patterns(target_key, assessment, domain)
        if assessment.overrides_applied:
            _log_override_corrections(target_key, assessment, domain)
        if os.environ.get("VERDX_FLYWHEEL_SYNC", "1") != "0":
            _sync_flywheel()
    return assessment


def quick_check(
    target: str | Path,
    baseline: Assessment,
    *,
    changed_only: bool = False,
    live: bool = True,
    profile: str = "standard",
    domain: str | None = None,
    flywheel: bool = True,
) -> Assessment:
    """Re-validate and return only findings not present in the baseline.

    Useful for tight fix-validate loops: run once to get a baseline,
    fix some findings, then quick_check to see only new problems.
    """
    current = validate(
        target,
        changed_only=changed_only,
        live=live,
        profile=profile,
        domain=domain,
        flywheel=flywheel,
    )

    baseline_sigs = {obs.signature for obs in baseline.observations}
    current_sigs = {obs.signature for obs in current.observations}

    new_sigs = current_sigs - baseline_sigs
    resolved_sigs = baseline_sigs - current_sigs
    persistent_sigs = baseline_sigs & current_sigs

    new_observations = tuple(obs for obs in current.observations if obs.signature in new_sigs)

    return Assessment(
        score=current.score,
        verdict=current.verdict,
        observations=new_observations,
        decision_hash=current.decision_hash,
        overrides_applied=current.overrides_applied,
        validators_run=current.validators_run,
        metadata={
            **current.metadata,
            "quick_check": {
                "new_count": len(new_sigs),
                "resolved_count": len(resolved_sigs),
                "persistent_count": len(persistent_sigs),
            },
        },
    )


def fix(
    target: str | Path,
    *,
    dry_run: bool = False,
    changed_only: bool = False,
    live: bool = True,
    profile: str = "standard",
    domain: str | None = None,
    max_iterations: int = 5,
    flywheel: bool = True,
) -> FixResult:
    """Apply deterministic fixes to a target.

    Discovers registered fixers, validates, applies matching fixes,
    and re-validates in a bounded loop.
    """
    from functools import partial

    from verdx.fix_loop import run_fix_loop
    from verdx.fixers import discover_fixers

    target_path = Path(target).resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"target does not exist: {target_path}")

    fixers = discover_fixers()
    if not fixers and not dry_run:
        validate_result = validate(
            target_path,
            live=live,
            profile=profile,
            domain=domain,
            changed_only=changed_only,
            flywheel=flywheel,
        )
        return FixResult(
            assessment_before=validate_result,
            assessment_after=validate_result,
            fixes_applied=(),
            iterations=1,
            remaining_observations=validate_result.observations,
            termination_reason="no_fixers",
        )

    validate_fn = partial(
        validate,
        live=live,
        profile=profile,
        domain=domain,
        changed_only=changed_only,
        flywheel=flywheel,
    )

    result = run_fix_loop(
        target=target_path,
        validate_fn=validate_fn,
        fixers=fixers,
        max_iterations=max_iterations,
        dry_run=dry_run,
    )

    use_flywheel = flywheel and os.environ.get("VERDX_FLYWHEEL", "1") != "0"
    if result.fixes_applied and not dry_run and use_flywheel:
        target_key = _resolve_target_key(target_path)
        _log_fix_patterns(target_key, result, domain)
        if os.environ.get("VERDX_FLYWHEEL_SYNC", "1") != "0":
            _sync_flywheel()

    return result


def drive(
    target: str | Path,
    fix_cmd: str,
    *,
    max_iterations: int = 5,
    live: bool = True,
    profile: str = "standard",
    domain: str | None = None,
    flywheel: bool = True,
) -> DriveResult:
    """Drive an external fix command to repair a target until verdx's own validation passes.

    verdx holds the loop and the verdict. The external fix command is invoked only to repair
    findings, and only while verdx's verdict is ``block``. Returns once the verdict is
    ``pass`` or the loop terminates for a bounded reason (exhausted iterations, fix-command
    error, or no progress). The fix command never decides completion, so this is a gate,
    not a step the fix command is trusted to run.
    """
    from functools import partial

    from verdx._drive import default_fix_fn, run_drive_loop

    target_path = Path(target).resolve()
    if not target_path.is_dir():
        raise NotADirectoryError(f"target must be a directory: {target_path}")

    validate_fn = partial(
        validate,
        target_path,
        live=live,
        profile=profile,
        domain=domain,
        flywheel=flywheel,
    )
    return run_drive_loop(
        target=target_path,
        validate_fn=validate_fn,
        fix_fn=default_fix_fn(fix_cmd),
        max_iterations=max_iterations,
    )


def _enrich_with_flywheel(
    assessment: Assessment, target_key: str, domain: str | None
) -> Assessment:
    """Annotate observations with flywheel pattern matches."""
    try:
        from verdx.engine.flywheel import Flywheel, FlywheelStore

        store = FlywheelStore(target_key)
        fw = Flywheel(store)
        annotations = fw.annotate(assessment.observations, domain=domain)

        matched = [a for a in annotations if a.pattern_id]
        if not matched and not any(a.suggested_decision for a in annotations):
            return assessment

        return Assessment(
            score=assessment.score,
            verdict=assessment.verdict,
            observations=assessment.observations,
            decision_hash=assessment.decision_hash,
            overrides_applied=assessment.overrides_applied,
            validators_run=assessment.validators_run,
            metadata={
                **assessment.metadata,
                "flywheel": {
                    "annotations": [
                        asdict(a) for a in annotations if a.pattern_id or a.suggested_decision
                    ],
                    "patterns_searched": len(store.load_patterns()),
                },
            },
        )
    except Exception:
        _log.debug("flywheel annotation skipped", exc_info=True)
        return assessment


def _log_override_corrections(target_key: str, assessment: Assessment, domain: str | None) -> None:
    """Log applied overrides as flywheel corrections so the flywheel learns resolution patterns."""
    try:
        from verdx.engine.flywheel import Flywheel, FlywheelStore

        fw = Flywheel(FlywheelStore(target_key))
        for override in assessment.overrides_applied:
            fw.log_correction(
                observation_signature=override.signature,
                target_key=target_key,
                initial_severity="finding",
                final_decision=override.decision,
                description=override.justification,
                domain=domain or "unknown",
            )
    except Exception:
        _log.debug("flywheel override correction logging skipped", exc_info=True)


def _log_validation_patterns(target_key: str, assessment: Assessment, domain: str | None) -> None:
    """Log validation findings as flywheel patterns so the flywheel learns across repos."""
    try:
        from verdx.engine.flywheel import Flywheel, FlywheelStore

        fw = Flywheel(FlywheelStore(target_key))
        for obs in assessment.observations:
            if obs.disposition is not None:
                continue
            fw.log_pattern(
                target_key=target_key,
                symptoms=obs.message,
                diagnosis=f"[{obs.severity}] {obs.category}: {obs.message}",
                resolution="pending",
                category=obs.category,
                domain=domain or "unknown",
                observation_signatures=[obs.signature],
                source=f"validate:{obs.category}",
            )
    except Exception:
        _log.debug("flywheel validation pattern logging skipped", exc_info=True)


def _sync_flywheel() -> None:
    """Auto-commit flywheel data to verdx-data repo (best-effort, never raises)."""
    from verdx._flywheel_sync import sync_flywheel

    sync_flywheel()


def _log_fix_patterns(target_key: str, result: FixResult, domain: str | None) -> None:
    """Log successful fixes as flywheel patterns."""
    try:
        from verdx.engine.flywheel import Flywheel, FlywheelStore

        fw = Flywheel(FlywheelStore(target_key))
        for action in result.fixes_applied:
            fw.log_pattern(
                target_key=target_key,
                symptoms=action.description,
                diagnosis=f"Resolved by {action.fixer_id}",
                resolution=action.description,
                category=f"auto-fix-{action.fixer_id}",
                domain=domain or "unknown",
                observation_signatures=[action.observation_signature],
                source=f"auto-fix:{action.fixer_id}",
            )
    except Exception:
        _log.debug("flywheel pattern logging skipped", exc_info=True)


def _overrides_to_dispositions(overrides: list[Override]) -> dict[str, Disposition]:
    """Bridge porcelain overrides to engine dispositions, keyed by fingerprint.

    Lets the porcelain layer feed suppression into the engine, where it is applied
    decision-aware before scoring, instead of removing findings after the fact.
    """
    from verdx.contracts.disposition import DISPOSITION_SCHEMA_VERSION, Disposition

    return {
        ov.signature: Disposition(
            schema_version=DISPOSITION_SCHEMA_VERSION,
            issue_fingerprint=ov.signature,
            decision=ov.decision,
            reason=ov.justification,
            team=ov.by,
            timestamp=ov.date,
            expires=ov.expires,
        )
        for ov in overrides
    }
