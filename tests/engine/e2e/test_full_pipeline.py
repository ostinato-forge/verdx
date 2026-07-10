"""End-to-end tests: full validation pipeline from config to report.

Each test exercises: config -> validators -> engine -> store -> history.
They run against the built-in ``markdown`` gate, so no external domain package
is required.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from verdx.contracts.dimensions import CoverageStatus, EscalationLevel
from verdx.contracts.disposition import DISPOSITION_SCHEMA_VERSION, Disposition
from verdx.domains.markdown.validators import get_validators
from verdx.engine.runtime.config import load_domain_config
from verdx.engine.runtime.engine import RunConfig, run_validation
from verdx.engine.runtime.store.history import HistoryStore, ObservationRecord, RunRecord
from verdx.engine.runtime.store.runs import RunStore

if TYPE_CHECKING:
    from pathlib import Path


def _content_hash(path: Path) -> str:
    """Compute a content hash for a target directory."""
    h = hashlib.sha256()
    for f in sorted(path.rglob("*")):
        if f.is_file():
            h.update(f.read_bytes())
    return f"sha256:{h.hexdigest()[:16]}"


def _run(
    target: Path,
    state_dir: Path,
    run_id: str,
    *,
    previous_fingerprints: set[str] | None = None,
    previous_score: int | None = None,
    active_dispositions: dict | None = None,
) -> tuple:
    """Execute a full validation run and persist results."""
    cfg = load_domain_config("markdown")
    content_hash = _content_hash(target)
    target_key = str(target)

    config = RunConfig(
        run_id=run_id,
        target_key=target_key,
        target_path=str(target),
        target_content_hash=content_hash,
        timestamp="2026-04-30T12:00:00Z",
        producer_role="content-author",
        producer_id=None,
        policy=cfg.policy,
        validators=get_validators(),
        critical_validator_ids=cfg.critical_validators,
        active_dispositions=active_dispositions or {},
        previous_fingerprints=previous_fingerprints,
        previous_score=previous_score,
        timeout_seconds=cfg.timeout_seconds,
        dimension_map=cfg.dimension_map,
    )

    outcome = run_validation(config)

    run_store = RunStore(state_dir)
    run_store.create_run(run_id, "2026-04-30T12:00:00Z")
    run_store.complete_run(
        run_id,
        "2026-04-30T12:01:00Z",
        assessment_json=outcome.assessment_json,
        report_md=outcome.report_md,
        subject_coverage_json=json.dumps(
            [record.to_dict() for record in outcome.assessment.subject_coverage],
            indent=2,
            sort_keys=True,
        ),
    )

    history = HistoryStore(state_dir)
    history.append_run(
        target_key,
        RunRecord(
            run_id=run_id,
            timestamp="2026-04-30T12:00:00Z",
            target_content_hash=content_hash,
            score=outcome.assessment.score.overall,
            observation_count=len(outcome.observations),
        ),
    )
    obs_records = [
        ObservationRecord(
            run_id=run_id,
            observation_id=o.observation_id,
            issue_fingerprint=o.issue_fingerprint,
            target_content_hash=content_hash,
            rule_id=o.rule_id,
            category=o.category,
            severity=o.severity.value,
            escalation=o.escalation.value,
        )
        for o in outcome.observations
    ]
    history.append_observations(target_key, obs_records)

    return outcome, cfg


# ===========================================================================
# Fixture builders
# ===========================================================================


def _make_clean_target(tmp_path: Path) -> Path:
    """A clean Markdown target with no hygiene issues."""
    target = tmp_path / "target"
    target.mkdir(parents=True)
    (target / "README.md").write_text(
        "# Project\n\n"
        "## Overview\n\n"
        "This project does a thing.\n\n"
        "## Setup\n\n"
        "See [the overview](README.md#overview).\n",
    )
    return target


def _make_broken_content_target(tmp_path: Path) -> Path:
    """A Markdown target with heading, link, and whitespace issues."""
    target = tmp_path / "target"
    target.mkdir(parents=True)
    # h1 -> h3 skip (heading finding), broken relative link, trailing whitespace.
    (target / "README.md").write_text(
        "# Project\n### Skips h2 and has trailing space   \nSee [the missing doc](missing.md).\n",
    )
    return target


def _make_structure_only_target(tmp_path: Path) -> Path:
    """A target whose only issue is a heading-hierarchy (structure) violation."""
    target = tmp_path / "target"
    target.mkdir(parents=True)
    (target / "README.md").write_text("# Project\n\n#### Jumps to h4\n\nBody text.\n")
    return target


# ===========================================================================
# E2E tests
# ===========================================================================


class TestCleanTarget:
    def test_scores_100(self, tmp_path: Path):
        target = _make_clean_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-clean-001")
        assert outcome.assessment.score.overall == 100
        assert len(outcome.observations) == 0

    def test_coverage_full(self, tmp_path: Path):
        target = _make_clean_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-clean-002")
        assert outcome.assessment.coverage_status == CoverageStatus.FULL

    def test_report_contains_score(self, tmp_path: Path):
        target = _make_clean_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-clean-003")
        assert "100/100" in outcome.report_md

    def test_assessment_json_valid(self, tmp_path: Path):
        target = _make_clean_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-clean-004")
        data = json.loads(outcome.assessment_json)
        assert data["score"]["overall"] == 100
        assert data["coverage_status"] == "full"

    def test_run_persisted(self, tmp_path: Path):
        target = _make_clean_target(tmp_path)
        state = tmp_path / "state"
        _run(target, state, "run-clean-005")
        store = RunStore(state)
        status = store.get_status("run-clean-005")
        assert status is not None
        assert status.status == "completed"
        assert store.get_report("run-clean-005") is not None
        assert store.get_assessment("run-clean-005") is not None

    def test_subject_coverage_persisted(self, tmp_path: Path):
        target = _make_clean_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-clean-subjects")
        store = RunStore(state)
        raw = store.get_subject_coverage("run-clean-subjects")
        assert raw is not None
        assert json.loads(raw) == [
            record.to_dict() for record in outcome.assessment.subject_coverage
        ]

    def test_history_appended(self, tmp_path: Path):
        target = _make_clean_target(tmp_path)
        state = tmp_path / "state"
        _run(target, state, "run-clean-006")
        history = HistoryStore(state)
        runs = history.get_runs(str(target))
        assert len(runs) == 1
        assert runs[0].score == 100


class TestBrokenContent:
    def test_produces_observations(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-broken-001")
        assert len(outcome.observations) >= 2
        assert outcome.assessment.score.overall < 100

    def test_heading_violation_found(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-broken-002")
        headings = [o for o in outcome.observations if o.rule_id == "md-headings:check"]
        assert len(headings) == 1

    def test_broken_link_found(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-broken-003")
        links = [o for o in outcome.observations if o.rule_id == "md-links:check"]
        assert len(links) == 1

    def test_report_mentions_findings(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-broken-004")
        assert "md-headings:check" in outcome.report_md
        assert "md-links:check" in outcome.report_md


class TestStructureFinding:
    def test_heading_finding(self, tmp_path: Path):
        target = _make_structure_only_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-structure-001")
        headings = [o for o in outcome.observations if o.rule_id == "md-headings:check"]
        assert len(headings) == 1

    def test_escalation_applied(self, tmp_path: Path):
        target = _make_structure_only_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-structure-002")
        headings = [o for o in outcome.observations if o.rule_id == "md-headings:check"]
        assert headings[0].escalation in (
            EscalationLevel.L1,
            EscalationLevel.L2,
            EscalationLevel.L3,
        )


class TestCriticalValidatorFailure:
    def test_degraded_coverage(self, tmp_path: Path):
        target = _make_clean_target(tmp_path)
        cfg = load_domain_config("markdown")

        def _crashing_validator(ctx):
            raise RuntimeError("boom")

        _crashing_validator.validator_id = "md-headings"  # type: ignore[attr-defined]
        _crashing_validator.validator_version = "1.0.0"  # type: ignore[attr-defined]

        config = RunConfig(
            run_id="run-crash-001",
            target_key=str(target),
            target_path=str(target),
            target_content_hash="sha256:crash",
            timestamp="2026-04-30T12:00:00Z",
            producer_role="content-author",
            producer_id=None,
            policy=cfg.policy,
            validators=[(_crashing_validator, {})],
            # Make the crashing validator critical so the failure degrades coverage.
            critical_validator_ids=frozenset({"md-headings"}),
            active_dispositions={},
            previous_fingerprints=None,
            previous_score=None,
        )
        outcome = run_validation(config)
        assert outcome.assessment.coverage_status == CoverageStatus.DEGRADED
        assert len(outcome.recovery_items) == 1
        assert outcome.recovery_items[0].kind == "retryable"
        assert outcome.assessment.score.overall < 100


class TestDisposition:
    def test_accepted_risk_applied(self, tmp_path: Path):
        target = _make_structure_only_target(tmp_path)
        state = tmp_path / "state"

        outcome1, _ = _run(target, state, "run-disp-001")
        heading_obs = [o for o in outcome1.observations if o.rule_id == "md-headings:check"]
        assert len(heading_obs) == 1
        fp = heading_obs[0].issue_fingerprint

        disp = Disposition(
            schema_version=DISPOSITION_SCHEMA_VERSION,
            issue_fingerprint=fp,
            decision="accepted-risk",
            reason="Known: heading level intentional for this target",
            team="docs",
            timestamp="2026-04-30T12:00:00Z",
        )

        outcome2, _ = _run(
            target,
            state,
            "run-disp-002",
            active_dispositions={fp: disp},
        )

        matching = [o for o in outcome2.observations if o.issue_fingerprint == fp]
        assert len(matching) == 1
        assert matching[0].disposition is not None
        assert matching[0].disposition.decision == "accepted-risk"


class TestImprovementTracking:
    def test_second_run_detects_improvement(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"

        outcome1, _ = _run(target, state, "run-imp-001")
        fps1 = {o.issue_fingerprint for o in outcome1.observations}
        score1 = outcome1.assessment.score.overall
        assert len(fps1) >= 2

        fixed_target = _make_clean_target(tmp_path / "fixed")
        outcome2, _ = _run(
            fixed_target,
            state,
            "run-imp-002",
            previous_fingerprints=fps1,
            previous_score=score1,
        )

        assert outcome2.assessment.improvement is not None
        assert outcome2.assessment.improvement.resolved_count >= 2
        assert outcome2.assessment.score.comparison is not None
        assert outcome2.assessment.score.comparison.previous_score == score1
        assert outcome2.assessment.score.overall > score1

    def test_regression_detected(self, tmp_path: Path):
        clean_target = _make_clean_target(tmp_path / "clean")
        state = tmp_path / "state"

        outcome1, _ = _run(clean_target, state, "run-reg-001")
        fps1 = {o.issue_fingerprint for o in outcome1.observations}
        score1 = outcome1.assessment.score.overall
        assert score1 == 100

        broken_target = _make_broken_content_target(tmp_path / "broken")
        outcome2, _ = _run(
            broken_target,
            state,
            "run-reg-002",
            previous_fingerprints=fps1,
            previous_score=score1,
        )

        assert outcome2.assessment.score.overall < score1
        assert outcome2.assessment.improvement is not None
        assert outcome2.assessment.improvement.new_count >= 2


class TestHistoryIntegration:
    def test_multiple_runs_tracked(self, tmp_path: Path):
        target = _make_clean_target(tmp_path)
        state = tmp_path / "state"

        _run(target, state, "run-hist-001")
        _run(target, state, "run-hist-002")

        history = HistoryStore(state)
        runs = history.get_runs(str(target))
        assert len(runs) == 2
        assert runs[0].run_id == "run-hist-001"
        assert runs[1].run_id == "run-hist-002"

    def test_observation_history_stored(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"

        _run(target, state, "run-obs-hist-001")

        history = HistoryStore(state)
        obs = history.get_observations(str(target))
        assert len(obs) >= 2

    def test_previous_fingerprints_from_history(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"

        _run(target, state, "run-fp-001")

        history = HistoryStore(state)
        fps = history.get_previous_fingerprints(str(target))
        assert len(fps) >= 2


class TestReportSchema:
    def test_markdown_starts_with_assessment(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-schema-001")
        assert outcome.report_md.startswith("# Assessment:")

    def test_json_has_required_fields(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-schema-002")
        data = json.loads(outcome.assessment_json)
        required = {
            "schema_version",
            "run_id",
            "target_key",
            "coverage_status",
            "observations",
            "score",
            "escalation_summary",
        }
        assert required.issubset(data.keys())

    def test_observations_have_provenance(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-schema-003")
        for obs in outcome.observations:
            assert obs.observation_id
            assert obs.issue_fingerprint
            assert obs.validator_id
            assert obs.independence.result == "satisfied"

    def test_no_actor_identity_in_markdown(self, tmp_path: Path):
        target = _make_broken_content_target(tmp_path)
        state = tmp_path / "state"
        outcome, _ = _run(target, state, "run-schema-004")
        assert "producer_id" not in outcome.report_md
        assert "actor_kind" not in outcome.report_md
