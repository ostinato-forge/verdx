"""Tests for the runner engine — the I/O orchestration layer."""

from __future__ import annotations

from verdx.contracts.dimensions import (
    Confidence,
    CoverageStatus,
    DimLevel,
    EscalationLevel,
    Severity,
)
from verdx.contracts.observation import ObservationDraft
from verdx.contracts.validator import ValidatorResult
from verdx.engine.runtime.engine import (
    RunConfig,
    run_validation,
)
from verdx.engine.validation.escalation import (
    EscalationPolicy,
    EscalationRule,
    RuleMatch,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_policy() -> EscalationPolicy:
    return EscalationPolicy(
        version=1,
        floors=(),
        rules=(
            EscalationRule(
                name="high-risk",
                match=RuleMatch(
                    quantifier="any",
                    dimension="*",
                    level=DimLevel.HIGH,
                ),
                level=EscalationLevel.L3,
            ),
            EscalationRule(
                name="low-risk",
                match=RuleMatch(
                    quantifier="all",
                    dimension="*",
                    level=DimLevel.LOW,
                ),
                level=EscalationLevel.L0,
            ),
        ),
    )


def _draft(**overrides) -> ObservationDraft:
    defaults = {
        "target_key": "sample/ch04/ge01",
        "location": "step:1",
        "rule_id": "yaml-syntax",
        "category": "correctness",
        "severity": Severity.HIGH,
        "confidence": Confidence.CONFIRMED,
        "dimensions": {"correctness": DimLevel.HIGH},
        "summary": "YAML syntax error",
    }
    return ObservationDraft(**(defaults | overrides))


def _successful_validator(drafts=None):
    """Create a validator that returns successfully."""
    d = drafts if drafts is not None else [_draft()]

    def validate(ctx):
        return ValidatorResult(
            validator_id="yaml-lint",
            validator_version="1.0.0",
            drafts=d,
            checks_run=10,
            checks_passed=9,
            duration_ms=150,
            status="completed",
        )

    validate.validator_id = "yaml-lint"
    validate.validator_version = "1.0.0"
    return validate


def _failing_validator():
    """Create a validator that crashes."""

    def validate(ctx):
        raise RuntimeError("parser crashed")

    validate.validator_id = "crash-lint"
    validate.validator_version = "2.0.0"
    return validate


def _skipping_validator():
    """Create a validator that skips."""

    def validate(ctx):
        return ValidatorResult(
            validator_id="optional-lint",
            validator_version="1.0.0",
            drafts=[],
            checks_run=0,
            checks_passed=0,
            duration_ms=5,
            status="skipped",
            skip_reason="not applicable",
        )

    validate.validator_id = "optional-lint"
    validate.validator_version = "1.0.0"
    return validate


def _slow_validator(sleep_seconds: float):
    """Create a validator that sleeps before returning."""

    def validate(ctx):
        import time

        time.sleep(sleep_seconds)
        return ValidatorResult(
            validator_id="slow-lint",
            validator_version="1.0.0",
            drafts=[_draft()],
            checks_run=5,
            checks_passed=5,
            duration_ms=int(sleep_seconds * 1000),
            status="completed",
        )

    validate.validator_id = "slow-lint"
    validate.validator_version = "1.0.0"
    return validate


def _config(**overrides) -> RunConfig:
    defaults = {
        "run_id": "run-001",
        "target_key": "sample/ch04/ge01",
        "target_path": "/tmp/target",
        "target_content_hash": "sha256:abc123",
        "timestamp": "2026-04-30T12:00:00Z",
        "producer_role": "content-author",
        "producer_id": None,
        "policy": _default_policy(),
        "validators": [(_successful_validator(), {})],
        "critical_validator_ids": frozenset(),
        "active_dispositions": {},
        "previous_fingerprints": None,
        "previous_score": None,
    }
    return RunConfig(**(defaults | overrides))


# ===========================================================================
# Full run
# ===========================================================================


class TestRunValidation:
    def test_clean_run(self):
        cfg = _config(
            validators=[(_successful_validator(drafts=[]), {})],
        )
        outcome = run_validation(cfg)
        assert outcome.assessment.score.overall == 100
        assert len(outcome.observations) == 0
        assert "100/100" in outcome.report_md

    def test_run_with_observations(self):
        cfg = _config()
        outcome = run_validation(cfg)
        assert len(outcome.observations) == 1
        assert outcome.assessment.score.overall < 100
        assert outcome.observations[0].escalation == EscalationLevel.L3

    def test_failed_validator_yields_coverage_gap(self):
        cfg = _config(
            validators=[(_failing_validator(), {})],
        )
        outcome = run_validation(cfg)
        assert len(outcome.observations) == 0
        assert len(outcome.assessment.coverage_gaps) > 0
        assert outcome.assessment.score.overall < 100

    def test_failed_critical_validator_degrades_coverage(self):
        cfg = _config(
            validators=[(_failing_validator(), {})],
            critical_validator_ids=frozenset({"crash-lint"}),
        )
        outcome = run_validation(cfg)
        assert outcome.assessment.coverage_status == CoverageStatus.DEGRADED

    def test_skipped_validator_partial(self):
        cfg = _config(
            validators=[
                (_successful_validator(drafts=[]), {}),
                (_skipping_validator(), {}),
            ],
        )
        outcome = run_validation(cfg)
        assert outcome.assessment.coverage_status == CoverageStatus.PARTIAL

    def test_run_produces_json_and_markdown(self):
        cfg = _config()
        outcome = run_validation(cfg)
        assert outcome.assessment_json.startswith("{")
        assert outcome.report_md.startswith("# Assessment:")

    def test_previous_fingerprints_tracked(self):
        cfg = _config(
            previous_fingerprints={"fp-old-1", "fp-old-2"},
        )
        outcome = run_validation(cfg)
        assert outcome.assessment.improvement is not None

    def test_previous_score_compared(self):
        cfg = _config(
            validators=[(_successful_validator(drafts=[]), {})],
            previous_score=80,
        )
        outcome = run_validation(cfg)
        assert outcome.assessment.score.comparison is not None
        assert outcome.assessment.score.comparison.previous_score == 80

    def test_evidence_bounded(self):
        huge_draft = _draft(
            evidence=(
                __import__("verdx.contracts.observation", fromlist=["Evidence"]).Evidence(
                    source="lint",
                    kind="error",
                    details="x" * 100000,
                    data={"content": "y" * 100000},
                ),
            ),
        )
        cfg = _config(
            validators=[(_successful_validator(drafts=[huge_draft]), {})],
        )
        outcome = run_validation(cfg)
        assert len(outcome.observations) == 1
        obs = outcome.observations[0]
        assert any(e.truncated for e in obs.evidence)

    def test_recovery_items_attached(self):
        cfg = _config(
            validators=[(_failing_validator(), {})],
        )
        outcome = run_validation(cfg)
        assert len(outcome.recovery_items) == 1
        assert outcome.recovery_items[0].kind == "retryable"

    def test_subject_discovery_crash_recovery(self):
        def _broken_discovery(path, target_key):
            raise RuntimeError("cannot parse model")

        cfg = _config(
            validators=[(_successful_validator(drafts=[]), {})],
            subject_discovery=_broken_discovery,
        )
        outcome = run_validation(cfg)
        assert outcome.recovery_items[-1].kind == "discovery-failed"
        assert outcome.recovery_items[-1].validator_id == "subject-discovery"
        assert outcome.assessment.coverage_status == CoverageStatus.DEGRADED
        assert all(
            status.validator_id != "subject-discovery"
            for status in outcome.assessment.validator_statuses
        )
        assert all(
            gap.validator_id != "subject-discovery" for gap in outcome.assessment.coverage_gaps
        )

    def test_active_dispositions_passed_to_core(self):
        from verdx.contracts.disposition import (
            DISPOSITION_SCHEMA_VERSION,
            Disposition,
        )
        from verdx.contracts.independence import IndependenceRecord
        from verdx.engine.validation.canonicalize import Provenance, canonicalize

        d = _draft()
        prov = Provenance(
            producer_role="content-author",
            producer_id=None,
            validator_id="yaml-lint",
            validator_version="1.0.0",
            independence=IndependenceRecord(
                producer_role="content-author",
                validator_role="yaml-lint",
                actor_kind="agent",
                relation="independent",
                policy_id="verdx-v1",
                result="satisfied",
            ),
        )
        result = canonicalize(
            run_id="run-001",
            drafts=[d],
            provenance=prov,
            policy=_default_policy(),
            active_dispositions={},
        )
        fp = result[0].issue_fingerprint

        disp = Disposition(
            schema_version=DISPOSITION_SCHEMA_VERSION,
            issue_fingerprint=fp,
            decision="accepted-risk",
            reason="Known issue",
            team="training",
            timestamp="2026-04-30T12:00:00Z",
        )

        cfg = _config(active_dispositions={fp: disp})
        outcome = run_validation(cfg)
        matching = [o for o in outcome.observations if o.issue_fingerprint == fp]
        assert len(matching) == 1
        assert matching[0].disposition is not None
        assert matching[0].disposition.decision == "accepted-risk"


# ===========================================================================
# Diagnostic logging
# ===========================================================================


class TestDiagnosticLogging:
    def test_run_validation_emits_diagnostic_events(self, tmp_path):
        from verdx.engine.runtime.store.diagnostic import DiagnosticLog

        cfg = _config(validators=[(_successful_validator(), {})])
        log = DiagnosticLog(tmp_path, cfg.run_id, enabled=True)

        outcome = run_validation(cfg, diagnostic_log=log)
        assert outcome.assessment.score.overall > 0

        events = log.read_events()
        assert len(events) > 0

        # Should have run-start, validator-start, validator-complete, scoring, run-complete
        phases = [e.phase for e in events]
        assert "run-start" in phases
        assert "validator-start" in phases
        assert "validator-complete" in phases
        assert "scoring" in phases
        assert "run-complete" in phases

    def test_run_validation_without_diagnostic_log(self):
        """Backward compatibility: run_validation works without diagnostic log."""
        cfg = _config(validators=[(_successful_validator(drafts=[]), {})])
        outcome = run_validation(cfg)
        assert outcome.assessment.score.overall == 100

    def test_diagnostic_log_tracks_validator_failure(self, tmp_path):
        from verdx.engine.runtime.store.diagnostic import DiagnosticLog

        cfg = _config(validators=[(_failing_validator(), {})])
        log = DiagnosticLog(tmp_path, cfg.run_id, enabled=True)

        outcome = run_validation(cfg, diagnostic_log=log)
        assert len(outcome.recovery_items) > 0

        events = log.read_events()
        failed_events = [e for e in events if e.phase == "validator-failed"]
        assert len(failed_events) == 1
        assert failed_events[0].validator_id == "crash-lint"
        assert failed_events[0].status == "failed"

    def test_diagnostic_log_tracks_timeout(self, tmp_path):
        from verdx.engine.runtime.store.diagnostic import DiagnosticLog

        cfg = _config(
            validators=[(_slow_validator(sleep_seconds=0.15), {})],
            timeout_seconds=0,
        )
        log = DiagnosticLog(tmp_path, cfg.run_id, enabled=True)

        outcome = run_validation(cfg, diagnostic_log=log)
        assert len(outcome.recovery_items) > 0

        events = log.read_events()
        timeout_events = [e for e in events if e.phase == "validator-timeout"]
        assert len(timeout_events) == 1
        assert timeout_events[0].validator_id == "slow-lint"
        assert timeout_events[0].status == "failed"
        assert "elapsed_ms" in timeout_events[0].context
        assert "timeout_ms" in timeout_events[0].context


class TestCacheIntegration:
    def test_cache_miss_then_hit(self, tmp_path):
        """Validator results are cached; second run reuses them."""
        from verdx.engine.runtime.store.cache import CacheStore

        cache = CacheStore(tmp_path)
        cfg = _config(validators=[(_successful_validator(drafts=[]), {})])

        outcome1 = run_validation(cfg, cache_store=cache)
        assert outcome1.assessment.score.overall == 100

        outcome2 = run_validation(cfg, cache_store=cache)
        assert outcome2.assessment.score.overall == outcome1.assessment.score.overall

    def test_no_cache_flag_skips_cache(self, tmp_path):
        from verdx.engine.runtime.store.cache import CacheStore

        cache = CacheStore(tmp_path)
        cfg = _config(validators=[(_successful_validator(drafts=[]), {})])

        run_validation(cfg, cache_store=cache)

        cfg_no_cache = _config(
            validators=[(_successful_validator(drafts=[]), {})],
            no_cache=True,
        )
        outcome = run_validation(cfg_no_cache, cache_store=cache)
        assert outcome.assessment.score.overall == 100

    def test_content_change_invalidates_cache(self, tmp_path):
        from verdx.engine.runtime.store.cache import CacheStore

        cache = CacheStore(tmp_path)
        cfg1 = _config(
            target_content_hash="sha256:version1",
            validators=[(_successful_validator(drafts=[]), {})],
        )
        run_validation(cfg1, cache_store=cache)

        cfg2 = _config(
            target_content_hash="sha256:version2",
            validators=[(_successful_validator(drafts=[]), {})],
        )
        outcome2 = run_validation(cfg2, cache_store=cache)
        assert outcome2.assessment.score.overall == 100
        assert outcome2.assessment_json != ""

    def test_policy_change_reflected_with_cached_validators(self, tmp_path):
        """Changing escalation policy produces different assessment even
        when validator results come from cache. This is the key fix:
        the old cache returned a stale assessment; the new one always
        re-derives from current policy."""
        from verdx.engine.runtime.store.cache import CacheStore

        cache = CacheStore(tmp_path)

        # First run: lenient policy (LOW → L0)
        lenient_policy = EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="low-risk",
                    match=RuleMatch(
                        quantifier="all",
                        dimension="*",
                        level=DimLevel.LOW,
                    ),
                    level=EscalationLevel.L0,
                ),
            ),
        )
        cfg1 = _config(
            policy=lenient_policy,
            validators=[(_successful_validator(), {})],
        )
        outcome1 = run_validation(cfg1, cache_store=cache)

        # Second run: strict policy (all → L4), same content, same validators
        strict_policy = EscalationPolicy(
            version=1,
            floors=(),
            rules=(
                EscalationRule(
                    name="everything-blocking",
                    match=RuleMatch(
                        quantifier="any",
                        dimension="*",
                        level=DimLevel.LOW,
                    ),
                    level=EscalationLevel.L4,
                ),
            ),
        )
        cfg2 = _config(
            policy=strict_policy,
            validators=[(_successful_validator(), {})],
        )
        outcome2 = run_validation(cfg2, cache_store=cache)

        # Both runs have observations, but escalation levels differ
        assert len(outcome1.observations) > 0
        assert len(outcome2.observations) > 0
        # The escalation level should change with policy
        assert outcome1.observations[0].escalation != outcome2.observations[0].escalation

    def test_only_completed_validators_are_cached(self, tmp_path):
        """Failed validator results are NOT cached."""
        from verdx.engine.runtime.store.cache import CacheStore

        cache = CacheStore(tmp_path)
        cfg = _config(validators=[(_failing_validator(), {})])

        run_validation(cfg, cache_store=cache)

        # Verify no cache entry exists for the failed validator
        cached = cache.get("sha256:abc123", "crash-lint", "2.0.0")
        assert cached is None
