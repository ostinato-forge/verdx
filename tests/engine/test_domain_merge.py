"""WS2: multi-domain validation merges all detected domains (no last-wins).

R2-5: engine.validate ran every detected domain but returned only the last
assessment, silently dropping the others' findings.
"""

from __future__ import annotations

from dataclasses import dataclass

from tests.contracts.test_models import (
    _make_observation,
    _make_run_assessment,
    _make_validator_status,
)
from verdx.engine.validation.scoring import merge_assessments


def test_merge_unions_observations_and_statuses_from_all_domains() -> None:
    obs_a = _make_observation(issue_fingerprint="fp-a", rule_id="rule-a", validator_id="va")
    obs_b = _make_observation(issue_fingerprint="fp-b", rule_id="rule-b", validator_id="vb")
    a = _make_run_assessment(
        observations=(obs_a,),
        validator_statuses=(_make_validator_status(validator_id="va"),),
    )
    b = _make_run_assessment(
        observations=(obs_b,),
        validator_statuses=(_make_validator_status(validator_id="vb"),),
    )

    merged = merge_assessments([a, b], critical_validator_ids=frozenset(), dimension_map={})

    assert {o.issue_fingerprint for o in merged.observations} == {"fp-a", "fp-b"}
    assert {s.validator_id for s in merged.validator_statuses} == {"va", "vb"}
    assert merged.score.observation_count == 2
    assert merged.score.overall < 100  # both findings were re-scored over the union


def test_merge_single_assessment_preserves_its_findings() -> None:
    obs = _make_observation(issue_fingerprint="fp-a")
    a = _make_run_assessment(observations=(obs,))

    merged = merge_assessments([a], critical_validator_ids=frozenset(), dimension_map={})

    assert {o.issue_fingerprint for o in merged.observations} == {"fp-a"}


def test_merge_dedupes_findings_and_validators_shared_across_domains() -> None:
    shared = _make_observation(issue_fingerprint="fp-shared", rule_id="r", validator_id="shared")
    a = _make_run_assessment(
        observations=(shared,), validator_statuses=(_make_validator_status(validator_id="shared"),)
    )
    b = _make_run_assessment(
        observations=(shared,), validator_statuses=(_make_validator_status(validator_id="shared"),)
    )

    merged = merge_assessments([a, b], critical_validator_ids=frozenset(), dimension_map={})

    assert [o.issue_fingerprint for o in merged.observations] == ["fp-shared"]
    assert [s.validator_id for s in merged.validator_statuses] == ["shared"]


def test_validate_single_domain_returns_assessment_unchanged(monkeypatch, tmp_path) -> None:
    from verdx.engine import api as engine_api

    obs = _make_observation(issue_fingerprint="fp-only")
    a = _make_run_assessment(observations=(obs,))

    @dataclass
    class _Outcome:
        assessment: object

    class _Cfg:
        policy = None
        critical_validators: frozenset[str] = frozenset()
        dimension_map: dict[str, list[str]] = {}  # noqa: RUF012

    class _Spec:
        def load_default_config(self) -> _Cfg:
            return _Cfg()

    monkeypatch.setattr(engine_api, "detect_domains", lambda t: ["only"])
    monkeypatch.setattr(engine_api, "get_domain", lambda n: _Spec())
    monkeypatch.setattr(engine_api, "run_gate_workflow", lambda **kw: _Outcome(a))

    assert engine_api.validate(tmp_path) is a


def test_validate_threads_config_and_dispositions_to_workflow(monkeypatch, tmp_path) -> None:
    from tests.contracts.test_models import _make_disposition
    from verdx.engine import api as engine_api

    captured: dict = {}
    a = _make_run_assessment()

    @dataclass
    class _Outcome:
        assessment: object

    class _Cfg:
        policy = None
        critical_validators: frozenset[str] = frozenset({"crit-v"})
        dimension_map: dict[str, list[str]] = {"cat": ["dim"]}  # noqa: RUF012
        scoring_config = None

    class _Spec:
        name = "d1"

        def load_default_config(self) -> _Cfg:
            return _Cfg()

    monkeypatch.setattr(engine_api, "detect_domains", lambda t: ["d1"])
    monkeypatch.setattr(engine_api, "get_domain", lambda n: _Spec())

    def fake_workflow(**kw):
        captured.update(kw)
        return _Outcome(a)

    monkeypatch.setattr(engine_api, "run_gate_workflow", fake_workflow)

    dispositions = {"fp-x": _make_disposition()}
    engine_api.validate(tmp_path, active_dispositions=dispositions)

    assert captured["critical_validator_ids"] == frozenset({"crit-v"})
    assert captured["dimension_map"] == {"cat": ["dim"]}
    assert captured["active_dispositions"] is dispositions


def test_validate_merges_all_detected_domains(monkeypatch, tmp_path) -> None:
    from verdx.engine import api as engine_api

    obs1 = _make_observation(issue_fingerprint="fp-d1", rule_id="r1", validator_id="v1")
    obs2 = _make_observation(issue_fingerprint="fp-d2", rule_id="r2", validator_id="v2")
    a1 = _make_run_assessment(
        observations=(obs1,), validator_statuses=(_make_validator_status(validator_id="v1"),)
    )
    a2 = _make_run_assessment(
        observations=(obs2,), validator_statuses=(_make_validator_status(validator_id="v2"),)
    )

    @dataclass
    class _Outcome:
        assessment: object

    class _Cfg:
        policy = None
        critical_validators: frozenset[str] = frozenset()
        dimension_map: dict[str, list[str]] = {}  # noqa: RUF012

    class _Spec:
        def load_default_config(self) -> _Cfg:
            return _Cfg()

    monkeypatch.setattr(engine_api, "detect_domains", lambda t: ["d1", "d2"])
    monkeypatch.setattr(engine_api, "get_domain", lambda n: _Spec())
    results = iter([_Outcome(a1), _Outcome(a2)])
    monkeypatch.setattr(engine_api, "run_gate_workflow", lambda **kw: next(results))

    merged = engine_api.validate(tmp_path)

    assert {o.issue_fingerprint for o in merged.observations} == {"fp-d1", "fp-d2"}
