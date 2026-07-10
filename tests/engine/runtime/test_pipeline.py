"""Tests for pipeline configuration loading and gate evaluation."""

from __future__ import annotations

import textwrap
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from tests.contracts.test_models import _make_independence, _make_observation
from verdx.contracts.assessment import (
    AssessmentConfidence,
    EscalationSummary,
    RunAssessment,
    Score,
)
from verdx.contracts.config import ConfigError
from verdx.contracts.dimensions import CoverageStatus, EscalationLevel
from verdx.contracts.subjects import (
    SUBJECT_COVERAGE_SCHEMA_VERSION,
    SUBJECT_SCHEMA_VERSION,
    SubjectCoverageRecord,
    SubjectCoverageState,
    SubjectModelHealth,
    SubjectSelector,
    ValidationSubject,
)
from verdx.engine.runtime.pipeline import (
    PIPELINE_SCHEMA_VERSION,
    GateConfig,
    GateDecision,
    PipelineConfig,
    PipelineStage,
    StageAction,
    evaluate_gate,
    load_default_pipeline,
    load_pipeline,
)


def _make_assessment(score: int) -> RunAssessment:
    """Create a minimal RunAssessment with the given overall score."""
    return RunAssessment(
        schema_version="verdx.assessment.v1",
        run_id="run-test",
        target_key="test/target",
        target_content_hash="sha256:abc",
        timestamp="2026-05-01T00:00:00Z",
        coverage_status=CoverageStatus.FULL,
        validator_statuses=(),
        coverage_gaps=(),
        checks_run=10,
        checks_passed=10,
        validators_clean=2,
        observations=(),
        recovery_items=(),
        confidence=AssessmentConfidence.STRONG,
        score=Score(
            overall=score,
            dimension_scores={},
            observation_count=0,
            escalation_distribution={EscalationLevel.L0: 0},
            coverage_penalty=0,
        ),
        escalation_summary=EscalationSummary(
            counts={EscalationLevel.L0: 0},
            highest=EscalationLevel.L0,
        ),
    )


def _make_subject() -> ValidationSubject:
    return ValidationSubject(
        schema_version=SUBJECT_SCHEMA_VERSION,
        subject_key="outline:outline.yml",
        target_key="test/target",
        kind="outline",
        label="outline.yml",
        selector=SubjectSelector(kind="subject", values=("outline:outline.yml",)),
        location="outline.yml",
    )


def _make_subject_coverage(
    *,
    state: SubjectCoverageState,
    criticality: str = "critical",
) -> SubjectCoverageRecord:
    return SubjectCoverageRecord(
        schema_version=SUBJECT_COVERAGE_SCHEMA_VERSION,
        run_id="run-test",
        target_key="test/target",
        target_content_hash="sha256:abc",
        subject=_make_subject(),
        state=state,
        criticality=criticality,
        required_validator_ids=("training-structure",),
        completed_validator_ids=(),
        failed_validator_ids=(),
        skipped_validator_ids=(),
        checks_run=0,
        checks_passed=0,
        observation_fingerprints=(),
        coverage_gap_validator_ids=(),
        blocking_reason=f"{state.value}: training-structure",
        model_health=SubjectModelHealth(),
    )


def _with_subject_coverage(
    assessment: RunAssessment,
    *records: SubjectCoverageRecord,
) -> RunAssessment:
    return replace(assessment, subject_coverage=records)


# ===========================================================================
# GateConfig validation
# ===========================================================================


class TestGateConfig:
    def test_valid_score_gate(self):
        g = GateConfig(type="score", threshold=80)
        assert g.type == "score"
        assert g.threshold == 80

    def test_valid_human_gate(self):
        g = GateConfig(type="human")
        assert g.threshold is None

    def test_valid_score_human_gate(self):
        g = GateConfig(type="score+human", threshold=90, reviewers_from=("tech-lead",))
        assert g.reviewers_from == ("tech-lead",)

    def test_invalid_gate_type(self):
        with pytest.raises(ValueError, match="gate type must be one of"):
            GateConfig(type="magic")

    def test_score_gate_requires_threshold(self):
        with pytest.raises(ValueError, match="requires a threshold"):
            GateConfig(type="score")

    def test_threshold_out_of_range(self):
        with pytest.raises(ValueError, match="between 0 and 100"):
            GateConfig(type="score", threshold=101)

    def test_negative_approvals(self):
        with pytest.raises(ValueError, match="non-negative"):
            GateConfig(type="human", required_approvals=-1)


# ===========================================================================
# PipelineStage validation
# ===========================================================================


class TestPipelineStage:
    def test_valid_stage(self):
        s = PipelineStage(
            name="self-qa",
            description="QA checks",
            gate=GateConfig(type="score", threshold=80),
            validator_ids=("training-content",),
        )
        assert s.name == "self-qa"

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            PipelineStage(
                name="",
                description="",
                gate=GateConfig(type="human"),
            )


# ===========================================================================
# PipelineConfig validation
# ===========================================================================


class TestPipelineConfig:
    def _stage(self, name: str) -> PipelineStage:
        return PipelineStage(
            name=name,
            description="",
            gate=GateConfig(type="human"),
        )

    def test_valid_config(self):
        cfg = PipelineConfig(
            version=1,
            name="test",
            stages=(self._stage("s1"),),
        )
        assert cfg.name == "test"

    def test_wrong_version(self):
        with pytest.raises(ValueError, match="unsupported pipeline version"):
            PipelineConfig(version=99, name="test", stages=(self._stage("s1"),))

    def test_empty_name(self):
        with pytest.raises(ValueError, match="pipeline name"):
            PipelineConfig(version=1, name="", stages=(self._stage("s1"),))

    def test_no_stages(self):
        with pytest.raises(ValueError, match="at least one stage"):
            PipelineConfig(version=1, name="test", stages=())

    def test_duplicate_stage_names(self):
        with pytest.raises(ValueError, match="unique"):
            PipelineConfig(
                version=1,
                name="test",
                stages=(self._stage("s1"), self._stage("s1")),
            )

    def test_get_stage(self):
        cfg = PipelineConfig(
            version=1,
            name="test",
            stages=(self._stage("alpha"), self._stage("beta")),
        )
        assert cfg.get_stage("beta").name == "beta"

    def test_get_stage_not_found(self):
        cfg = PipelineConfig(
            version=1,
            name="test",
            stages=(self._stage("alpha"),),
        )
        with pytest.raises(KeyError, match="no stage named 'missing'"):
            cfg.get_stage("missing")


# ===========================================================================
# evaluate_gate
# ===========================================================================


class TestEvaluateGate:
    def test_score_gate_passes(self):
        gate = GateConfig(type="score", threshold=80)
        assessment = _make_assessment(87)
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is True
        assert decision.score == 87
        assert decision.threshold == 80
        assert ">=" in decision.reason

    def test_score_gate_fails(self):
        gate = GateConfig(type="score", threshold=80)
        assessment = _make_assessment(72)
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is False
        assert decision.score == 72
        assert "<" in decision.reason

    def test_score_gate_exact_threshold(self):
        gate = GateConfig(type="score", threshold=80)
        assessment = _make_assessment(80)
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is True

    def test_human_gate_always_blocks(self):
        gate = GateConfig(type="human")
        assessment = _make_assessment(100)
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is False
        assert "human approval required" in decision.reason
        assert decision.score == 100

    def test_score_human_gate_score_fails(self):
        gate = GateConfig(type="score+human", threshold=90)
        assessment = _make_assessment(85)
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is False
        assert "< threshold" in decision.reason

    def test_score_human_gate_score_passes_still_blocks(self):
        gate = GateConfig(type="score+human", threshold=90)
        assessment = _make_assessment(95)
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is False
        assert "human approval required" in decision.reason
        assert decision.score == 95

    def test_gate_can_require_subject_coverage(self):
        gate = GateConfig(type="score", threshold=80, require_subject_coverage=True)
        assessment = _make_assessment(95)
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is False
        assert "subject coverage required" in decision.reason

    def test_score_gate_blocks_critical_subject_debt(self):
        gate = GateConfig(type="score", threshold=80)
        assessment = _with_subject_coverage(
            _make_assessment(95),
            _make_subject_coverage(state=SubjectCoverageState.MISSING),
        )
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is False
        assert "outline:outline.yml=missing" in decision.reason

    def test_allowed_subject_debt_does_not_block_score_gate(self):
        gate = GateConfig(
            type="score",
            threshold=80,
            allowed_subject_debt=(SubjectCoverageState.MODEL_LIMITED,),
        )
        assessment = _with_subject_coverage(
            _make_assessment(95),
            _make_subject_coverage(state=SubjectCoverageState.MODEL_LIMITED),
        )
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is True

    def test_human_gate_reports_subject_debt(self):
        gate = GateConfig(type="human")
        assessment = _with_subject_coverage(
            _make_assessment(95),
            _make_subject_coverage(
                state=SubjectCoverageState.FAILED,
                criticality="standard",
            ),
        )
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is False
        assert "subject debt" in decision.reason

    def test_score_human_gate_blocks_subject_debt_before_human_approval(self):
        gate = GateConfig(type="score+human", threshold=80)
        assessment = _with_subject_coverage(
            _make_assessment(95),
            _make_subject_coverage(state=SubjectCoverageState.MISSING),
        )
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is False
        assert "subject coverage debt blocks gate" in decision.reason
        assert "human approval required" not in decision.reason

    def test_independence_gate_blocks_unmet(self):
        gate = GateConfig(type="score", threshold=80, require_independence=True)
        obs = _make_observation(
            independence=_make_independence(result="unmet", validator_role="v1"),
        )
        assessment = replace(_make_assessment(95), observations=(obs,))
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is False
        assert "independence requirement unmet" in decision.reason
        assert "v1" in decision.reason

    def test_independence_gate_passes_when_satisfied(self):
        gate = GateConfig(type="score", threshold=80, require_independence=True)
        obs = _make_observation(
            independence=_make_independence(result="satisfied"),
        )
        assessment = replace(_make_assessment(95), observations=(obs,))
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is True

    def test_independence_gate_not_checked_when_disabled(self):
        gate = GateConfig(type="score", threshold=80, require_independence=False)
        obs = _make_observation(
            independence=_make_independence(result="unmet"),
        )
        assessment = replace(_make_assessment(95), observations=(obs,))
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is True

    def test_independence_gate_lists_all_unmet_validators(self):
        gate = GateConfig(type="score+human", threshold=80, require_independence=True)
        obs1 = _make_observation(
            observation_id="obs-1",
            independence=_make_independence(result="unmet", validator_role="v-a"),
        )
        obs2 = _make_observation(
            observation_id="obs-2",
            independence=_make_independence(result="unmet", validator_role="v-b"),
        )
        assessment = replace(_make_assessment(95), observations=(obs1, obs2))
        decision = evaluate_gate(gate, assessment)
        assert decision.passed is False
        assert "v-a" in decision.reason
        assert "v-b" in decision.reason


# ===========================================================================
# YAML loading
# ===========================================================================


class TestLoadPipeline:
    def _write_pipeline(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "pipeline.yml"
        p.write_text(textwrap.dedent(content))
        return p

    def test_load_minimal_pipeline(self, tmp_path: Path):
        path = self._write_pipeline(
            tmp_path,
            """\
            version: 1
            pipeline:
              name: test-pipeline
              stages:
                - name: check
                  gate:
                    type: score
                    threshold: 70
            """,
        )
        cfg = load_pipeline(path)
        assert cfg.name == "test-pipeline"
        assert len(cfg.stages) == 1
        assert cfg.stages[0].gate.threshold == 70

    def test_load_full_pipeline(self, tmp_path: Path):
        path = self._write_pipeline(
            tmp_path,
            """\
            version: 1
            pipeline:
              name: full-pipeline
              stages:
                - name: self-qa
                  description: "QA checks"
                  validators:
                    - training-content
                    - training-style
                  gate:
                    type: score
                    threshold: 80
                  on_pass:
                    labels: [qa-passed]
                    advance: review
                  on_fail:
                    labels: [needs-work]
                    comment: report
                - name: review
                  description: "Human review"
                  gate:
                    type: human
                    required_approvals: 1
                    reviewers_from: [peer]
                  on_pass:
                    advance: merge
            """,
        )
        cfg = load_pipeline(path)
        assert cfg.name == "full-pipeline"
        assert len(cfg.stages) == 2

        qa = cfg.stages[0]
        assert qa.validator_ids == ("training-content", "training-style")
        assert qa.gate.threshold == 80
        assert qa.on_pass.labels == ("qa-passed",)
        assert qa.on_pass.advance == "review"
        assert qa.on_fail is not None
        assert qa.on_fail.comment == "report"

        review = cfg.stages[1]
        assert review.gate.type == "human"
        assert review.gate.reviewers_from == ("peer",)

    def test_load_subject_coverage_gate_fields(self, tmp_path: Path):
        path = self._write_pipeline(
            tmp_path,
            """\
            version: 1
            pipeline:
              name: subject-aware
              stages:
                - name: check
                  gate:
                    type: score
                    threshold: 80
                    require_subject_coverage: true
                    allowed_subject_debt: [model_limited]
                    subject_selector:
                      kind: kind
                      values: [outline]
            """,
        )
        cfg = load_pipeline(path)
        gate = cfg.stages[0].gate
        assert gate.require_subject_coverage is True
        assert gate.allowed_subject_debt == (SubjectCoverageState.MODEL_LIMITED,)
        assert gate.subject_selector == SubjectSelector(kind="kind", values=("outline",))

    def test_rejects_invalid_subject_debt_state(self, tmp_path: Path):
        path = self._write_pipeline(
            tmp_path,
            """\
            version: 1
            pipeline:
              name: bad
              stages:
                - name: check
                  gate:
                    type: score
                    threshold: 80
                    allowed_subject_debt: [magic]
            """,
        )
        with pytest.raises(ConfigError, match="unknown subject debt state"):
            load_pipeline(path)

    def test_wrong_version(self, tmp_path: Path):
        path = self._write_pipeline(
            tmp_path,
            """\
            version: 99
            pipeline:
              name: bad
              stages:
                - name: s
                  gate:
                    type: human
            """,
        )
        with pytest.raises(ConfigError, match="unsupported pipeline version"):
            load_pipeline(path)

    def test_missing_pipeline_key(self, tmp_path: Path):
        path = self._write_pipeline(
            tmp_path,
            """\
            version: 1
            """,
        )
        with pytest.raises(ConfigError, match="'pipeline' key"):
            load_pipeline(path)

    def test_missing_stage_gate(self, tmp_path: Path):
        path = self._write_pipeline(
            tmp_path,
            """\
            version: 1
            pipeline:
              name: bad
              stages:
                - name: no-gate
            """,
        )
        with pytest.raises(ConfigError, match="gate mapping"):
            load_pipeline(path)

    def test_invalid_gate_type(self, tmp_path: Path):
        path = self._write_pipeline(
            tmp_path,
            """\
            version: 1
            pipeline:
              name: bad
              stages:
                - name: s
                  gate:
                    type: magic
            """,
        )
        with pytest.raises(ConfigError, match="gate type"):
            load_pipeline(path)

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="Failed to read"):
            load_pipeline(tmp_path / "nonexistent.yml")


class TestLoadDefaultPipeline:
    """The default pipeline is assembled from whatever domains are registered.

    The engine names no domain. It enumerates the runtime domain registry
    and composes each registered domain's pipeline config into one.
    """

    @staticmethod
    def _fake_domain(name: str, raw_pipeline: dict) -> object:
        """Build a minimal DomainSpec whose load_pipeline_config returns raw."""
        from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult

        return DomainSpec(
            name=name,
            markers=(),
            load_default_config=lambda: None,  # type: ignore[arg-type,return-value]
            load_checks=lambda: (),
            load_profiles=lambda: (),
            discover_subjects=lambda _path, _target: SubjectDiscoveryResult(),
            load_pipeline_config=lambda: raw_pipeline,
        )

    @staticmethod
    def _raw_pipeline(pipeline_name: str, stage_name: str) -> dict:
        return {
            "version": PIPELINE_SCHEMA_VERSION,
            "pipeline": {
                "name": pipeline_name,
                "stages": [{"name": stage_name, "gate": {"type": "human"}}],
            },
        }

    def _register(self, monkeypatch, specs: dict) -> None:
        """Inject fake domains into the runtime registry for the test."""
        from verdx.engine.runtime import domains as domains_mod

        monkeypatch.setattr(domains_mod, "_discovered", dict(specs))
        monkeypatch.setattr(domains_mod, "_load_errors", {})

    def test_returns_valid_pipeline_config(self, monkeypatch):
        # Task 4 will register the two example gates and assert non-emptiness
        # against the real registry; here we assert the assembly mechanism
        # produces a valid PipelineConfig from whatever domains are registered.
        self._register(
            monkeypatch,
            {"alpha": self._fake_domain("alpha", self._raw_pipeline("a", "s1"))},
        )
        config = load_default_pipeline()
        assert config is not None
        assert isinstance(config, PipelineConfig)
        assert config.version == PIPELINE_SCHEMA_VERSION

    def test_composes_stages_from_all_registered_domains(self, monkeypatch):
        self._register(
            monkeypatch,
            {
                "alpha": self._fake_domain("alpha", self._raw_pipeline("a", "s1")),
                "beta": self._fake_domain("beta", self._raw_pipeline("b", "s2")),
            },
        )
        config = load_default_pipeline()
        stage_names = {s.name for s in config.stages}
        assert stage_names == {"s1", "s2"}

    def test_skips_domains_without_pipeline_config(self, monkeypatch):
        from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult

        no_pipeline = DomainSpec(
            name="nopipe",
            markers=(),
            load_default_config=lambda: None,  # type: ignore[arg-type,return-value]
            load_checks=lambda: (),
            load_profiles=lambda: (),
            discover_subjects=lambda _path, _target: SubjectDiscoveryResult(),
            load_pipeline_config=None,
        )
        self._register(
            monkeypatch,
            {
                "nopipe": no_pipeline,
                "alpha": self._fake_domain("alpha", self._raw_pipeline("a", "s1")),
            },
        )
        config = load_default_pipeline()
        assert {s.name for s in config.stages} == {"s1"}

    def test_disambiguates_colliding_stage_names_across_domains(self, monkeypatch):
        self._register(
            monkeypatch,
            {
                "alpha": self._fake_domain("alpha", self._raw_pipeline("a", "review")),
                "beta": self._fake_domain("beta", self._raw_pipeline("b", "review")),
            },
        )
        config = load_default_pipeline()
        # Both contribute a stage named "review"; the collision is resolved by
        # prefixing with the contributing domain's registered name (a runtime
        # value, not a literal), so the resulting stage names stay unique.
        names = {s.name for s in config.stages}
        assert len(names) == 2
        assert "review" in names

    @staticmethod
    def _raw_pipeline_with_transitions(pipeline_name: str, stage_names: list[str]) -> dict:
        """Build a multi-stage pipeline whose stages advance to the next by name."""
        stages = []
        for i, sname in enumerate(stage_names):
            stage: dict = {"name": sname, "gate": {"type": "human"}}
            if i + 1 < len(stage_names):
                stage["on_pass"] = {"advance": stage_names[i + 1]}
                stage["on_fail"] = {"advance": stage_names[i]}
            stages.append(stage)
        return {
            "version": PIPELINE_SCHEMA_VERSION,
            "pipeline": {"name": pipeline_name, "stages": stages},
        }

    def test_rewrites_transitions_after_renaming_colliding_stages(self, monkeypatch):
        # Two domains both name their stages "review" -> "publish". On collision
        # the assembler renames the stages; the transitions (on_pass.advance,
        # on_fail.advance) must be rewritten to the renamed targets, so every
        # advance still resolves to a real stage in the assembled pipeline.
        self._register(
            monkeypatch,
            {
                "alpha": self._fake_domain(
                    "alpha", self._raw_pipeline_with_transitions("a", ["review", "publish"])
                ),
                "beta": self._fake_domain(
                    "beta", self._raw_pipeline_with_transitions("b", ["review", "publish"])
                ),
            },
        )
        config = load_default_pipeline()
        stage_names = {s.name for s in config.stages}
        # Every advance target must resolve to a stage that exists.
        for stage in config.stages:
            if stage.on_pass.advance is not None:
                assert stage.on_pass.advance in stage_names, (
                    f"{stage.name}.on_pass.advance -> {stage.on_pass.advance} is dangling"
                )
            if stage.on_fail is not None and stage.on_fail.advance is not None:
                assert stage.on_fail.advance in stage_names

        # The renamed alpha "review" must advance to the renamed alpha "publish",
        # not beta's. Since alpha is registered first it keeps the bare names;
        # beta's collide and get prefixed.
        by_name = {s.name: s for s in config.stages}
        # beta's first stage is renamed; its advance must point at beta's second.
        beta_review = by_name["beta:review"]
        assert beta_review.on_pass.advance == "beta:publish"
        assert "beta:publish" in by_name

    def test_propagates_malformed_domain_pipeline(self, monkeypatch):
        # A domain that DOES provide pipeline config but it is malformed must
        # not be silently skipped; the error propagates.
        bad = self._fake_domain(
            "bad",
            {
                "version": PIPELINE_SCHEMA_VERSION,
                "pipeline": {"name": "bad", "stages": [{"name": "s1", "gate": {"type": "bogus"}}]},
            },
        )
        self._register(
            monkeypatch,
            {
                "bad": bad,
                "alpha": self._fake_domain("alpha", self._raw_pipeline("a", "s1")),
            },
        )
        with pytest.raises(ConfigError):
            load_default_pipeline()

    def test_raises_when_no_domain_provides_pipeline(self, monkeypatch):
        self._register(monkeypatch, {})
        with pytest.raises(ConfigError, match="no default pipeline"):
            load_default_pipeline()

    def test_source_hardcodes_no_domain_name(self):
        # The invariant: the engine's default loader must not name any domain
        # by a string literal. Assert the function source is free of the
        # previously hardcoded "training" coupling.
        import inspect

        source = inspect.getsource(load_default_pipeline)
        assert "training" not in source


# ===========================================================================
# StageResult serialization
# ===========================================================================


class TestStageResultToDict:
    def test_serializes_to_dict(self):
        from verdx.engine.runtime.pipeline import StageResult

        assessment = _make_assessment(87)
        result = StageResult(
            stage="self-qa",
            gate_decision=GateDecision(
                passed=True,
                reason="score 87 >= threshold 80",
                score=87,
                threshold=80,
            ),
            assessment=assessment,
            next_stage="peer-review",
            actions=StageAction(labels=("qa-passed",), advance="peer-review"),
        )
        d = result.to_dict()
        assert d["stage"] == "self-qa"
        assert d["gate_decision"]["passed"] is True
        assert d["gate_decision"]["score"] == 87
        assert d["next_stage"] == "peer-review"
        assert d["actions"]["labels"] == ["qa-passed"]
        assert "assessment" in d
