"""Translation layer: engine RunAssessment to porcelain Assessment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx._hash import DECISION_HASH_SCHEMA, compute_decision_hash
from verdx.engine.validation.gate import passes_gate
from verdx.types import Assessment, Observation

if TYPE_CHECKING:
    from verdx.contracts.assessment import RunAssessment
    from verdx.fixers import FixerSpec

PORCELAIN_SEVERITIES = frozenset({"critical", "high", "medium", "low"})


def _translate_observation(
    engine_obs: object,
    fixers: list[FixerSpec] | None = None,
) -> Observation | None:
    """Convert one engine Observation to porcelain, or None if filtered.

    A finding that carries a team disposition is marked and left not-fixable: it
    stays visible but the fix loop will not auto-fix something a human settled.
    """
    severity_str: str = engine_obs.severity.value  # type: ignore[attr-defined]
    if severity_str not in PORCELAIN_SEVERITIES:
        return None

    disposition: str | None = (
        engine_obs.disposition.decision  # type: ignore[attr-defined]
        if engine_obs.disposition is not None  # type: ignore[attr-defined]
        else None
    )

    obs = Observation(
        severity=severity_str,
        category=engine_obs.category,  # type: ignore[attr-defined]
        location=engine_obs.location,  # type: ignore[attr-defined]
        message=engine_obs.summary,  # type: ignore[attr-defined]
        fixable=False,
        signature=engine_obs.issue_fingerprint,  # type: ignore[attr-defined]
        disposition=disposition,
    )

    if fixers and disposition is None:
        for spec in fixers:
            if spec.matches(obs):
                obs = Observation(
                    severity=obs.severity,
                    category=obs.category,
                    location=obs.location,
                    message=obs.message,
                    fixable=True,
                    signature=obs.signature,
                    disposition=None,
                )
                break

    return obs


def translate_assessment(
    engine: RunAssessment,
    *,
    threshold: int,
    profile: str,
    fixers: list[FixerSpec] | None = None,
) -> Assessment:
    """Translate an engine RunAssessment into a porcelain Assessment.

    A pure view: suppression and scoring already happened in the engine. Every
    finding is shown; one that carries a team disposition is marked (so a block
    always has a visible reason and the fix loop can skip it), the engine score
    is carried unchanged, and the verdict uses the shared gate. overrides_applied
    is populated by the caller from the matching overrides.
    """
    translated: list[Observation] = []
    dispositioned_signatures: list[str] = []

    for engine_obs in engine.observations:
        if engine_obs.disposition is not None:
            dispositioned_signatures.append(
                f"{engine_obs.issue_fingerprint}:{engine_obs.disposition.decision}"
            )
        obs = _translate_observation(engine_obs, fixers=fixers)
        if obs is None:
            continue
        translated.append(obs)

    validator_ids = [
        vs.validator_id for vs in engine.validator_statuses if vs.status == "completed"
    ]
    score = engine.score.overall
    verdict = "pass" if passes_gate(engine, threshold) else "block"

    decision_hash = compute_decision_hash(
        target_content_hash=engine.target_content_hash,
        profile=profile,
        validator_ids=validator_ids,
        observation_signatures=[o.signature for o in translated],
        override_signatures=dispositioned_signatures,
        score=score,
        threshold=threshold,
        verdict=verdict,
        coverage_status=engine.coverage_status.value,
        confidence=engine.confidence.value,
        validator_results=[
            {key: value for key, value in status.to_dict().items() if key != "duration_ms"}
            for status in engine.validator_statuses
        ],
        coverage_gaps=[gap.to_dict() for gap in engine.coverage_gaps],
    )

    from verdx import __version__

    return Assessment(
        score=score,
        verdict=verdict,
        observations=tuple(translated),
        decision_hash=decision_hash,
        overrides_applied=(),
        validators_run=tuple(validator_ids),
        metadata={
            "target": engine.target_key,
            "profile": profile,
            "timestamp": engine.timestamp,
            "verdx_version": __version__,
            "decision_hash_schema": DECISION_HASH_SCHEMA,
            "gate": {
                "coverage_status": engine.coverage_status.value,
                "confidence": engine.confidence.value,
                "coverage_gaps": [
                    {"validator_id": g.validator_id, "reason": g.reason}
                    for g in engine.coverage_gaps
                ],
            },
        },
    )
