"""Tests for the bounded fix loop."""

from __future__ import annotations

import shutil
from pathlib import Path

from verdx.fix_loop import run_fix_loop
from verdx.fixers import FixerSpec
from verdx.types import Assessment, FixAction, Observation


def _assessment(
    score: int = 85,
    verdict: str = "pass",
    observations: tuple[Observation, ...] = (),
) -> Assessment:
    return Assessment(
        score=score,
        verdict=verdict,
        observations=observations,
        decision_hash="a" * 64,
        overrides_applied=(),
        validators_run=("v1",),
        metadata={"target": "test", "profile": "standard"},
    )


def _obs(category: str = "structure", signature: str = "sig1") -> Observation:
    return Observation(
        severity="high",
        category=category,
        location=None,
        message="test issue",
        fixable=False,
        signature=signature,
    )


class _CountingFixer:
    def __init__(self) -> None:
        self.fix_count = 0

    def can_fix(self, observation: Observation) -> bool:
        return True

    def fix(self, target: Path, observation: Observation) -> FixAction | None:
        self.fix_count += 1
        return FixAction(
            observation_signature=observation.signature,
            fixer_id="counting",
            description=f"fix #{self.fix_count}",
        )


class TestFixLoop:
    def test_no_observations_no_fixes(self, tmp_path: Path) -> None:
        clean = _assessment()
        result = run_fix_loop(
            target=tmp_path,
            validate_fn=lambda t: clean,
            fixers=[],
        )
        assert result.fixes_applied == ()
        assert result.iterations == 1
        assert result.assessment_before is clean
        assert result.assessment_after is clean

    def test_no_fixers_no_fixes(self, tmp_path: Path) -> None:
        obs = _obs()
        dirty = _assessment(observations=(obs,))
        result = run_fix_loop(
            target=tmp_path,
            validate_fn=lambda t: dirty,
            fixers=[],
        )
        assert result.fixes_applied == ()
        assert len(result.remaining_observations) == 1

    def test_applies_fix_and_revalidates(self, tmp_path: Path) -> None:
        obs = _obs()
        dirty = _assessment(score=70, verdict="block", observations=(obs,))
        clean = _assessment(score=100, verdict="pass")

        call_count = 0

        def mock_validate(t: Path) -> Assessment:
            nonlocal call_count
            call_count += 1
            return dirty if call_count == 1 else clean

        fixer = _CountingFixer()
        spec = FixerSpec(
            fixer_id="f1",
            description="test",
            categories=frozenset({"structure"}),
            fixer=fixer,
        )
        result = run_fix_loop(
            target=tmp_path,
            validate_fn=mock_validate,
            fixers=[spec],
        )
        assert len(result.fixes_applied) == 1
        assert result.assessment_after.score == 100
        assert fixer.fix_count == 1

    def test_dry_run_does_not_apply(self, tmp_path: Path) -> None:
        obs = _obs()
        dirty = _assessment(observations=(obs,))

        spec = FixerSpec(
            fixer_id="f1",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_CountingFixer(),
        )
        result = run_fix_loop(
            target=tmp_path,
            validate_fn=lambda t: dirty,
            fixers=[spec],
            dry_run=True,
        )
        assert len(result.fixes_applied) == 1
        assert "[dry-run]" in result.fixes_applied[0].description
        assert result.assessment_before is result.assessment_after

    def test_stops_at_max_iterations(self, tmp_path: Path) -> None:
        # Each round improves the score and leaves a fixable finding, so the
        # loop keeps going and is bounded only by max_iterations.
        scores = iter([70, 75, 80, 85, 90, 95])

        def improving_validate(t: Path) -> Assessment:
            return _assessment(
                score=next(scores),
                verdict="block",
                observations=(_obs(signature="s"),),
            )

        fixer = _CountingFixer()
        spec = FixerSpec(
            fixer_id="f1",
            description="test",
            categories=frozenset({"structure"}),
            fixer=fixer,
        )
        result = run_fix_loop(
            target=tmp_path,
            validate_fn=improving_validate,
            fixers=[spec],
            max_iterations=3,
        )
        assert result.iterations == 3
        assert fixer.fix_count == 3
        assert result.assessment_after.score == 85

    def test_stops_when_no_improvement(self, tmp_path: Path) -> None:
        # A fixer that produces actions but does not raise the score stops the
        # loop immediately instead of spinning to max_iterations.
        dirty = _assessment(score=70, verdict="block", observations=(_obs(signature="s"),))
        spec = FixerSpec(
            fixer_id="f1",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_CountingFixer(),
        )
        result = run_fix_loop(
            target=tmp_path,
            validate_fn=lambda t: dirty,
            fixers=[spec],
            max_iterations=5,
        )
        assert result.iterations == 1
        assert result.fixes_applied == ()
        assert result.assessment_after.score == 70

    def test_reports_why_it_stopped(self, tmp_path: Path) -> None:
        # A fixer that never improves the score stops the loop on no improvement,
        # and the result says so (R2-8).
        dirty = _assessment(score=70, verdict="block", observations=(_obs(signature="s"),))
        spec = FixerSpec(
            fixer_id="f1",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_CountingFixer(),
        )
        result = run_fix_loop(target=tmp_path, validate_fn=lambda t: dirty, fixers=[spec])
        assert result.termination_reason == "no_improvement"

    def test_raising_fixer_is_isolated(self, tmp_path: Path) -> None:
        # A fixer that raises must not crash the run; the finding is left for the
        # residual instead (R2-2).
        dirty = _assessment(score=70, verdict="block", observations=(_obs(signature="s"),))

        class _BoomFixer:
            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction | None:
                raise RuntimeError("boom")

        spec = FixerSpec(
            fixer_id="boom",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_BoomFixer(),
        )
        result = run_fix_loop(target=tmp_path, validate_fn=lambda t: dirty, fixers=[spec])
        assert result.fixes_applied == ()
        assert len(result.remaining_observations) == 1

    def test_undoes_silent_mutation_when_fixer_returns_none(self, tmp_path: Path) -> None:
        # A fixer that writes files but returns None has its writes undone (R2-7).
        doc = tmp_path / "doc.txt"
        doc.write_text("original")
        dirty = _assessment(score=70, verdict="block", observations=(_obs(signature="s"),))

        class _SneakyFixer:
            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction | None:
                (target / "doc.txt").write_text("mutated")
                return None

        spec = FixerSpec(
            fixer_id="sneaky",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_SneakyFixer(),
        )
        run_fix_loop(target=tmp_path, validate_fn=lambda t: dirty, fixers=[spec])
        assert doc.read_text() == "original"

    def test_keeps_good_fix_and_drops_bad_in_same_pass(self, tmp_path: Path) -> None:
        """Per-fix: a good fix is kept even when a sibling fix in the same pass
        regresses; the bad one is rolled back individually (R2-2/R2-7/#8)."""
        a = tmp_path / "a.txt"
        a.write_text("orig")
        b = tmp_path / "b.txt"
        b.write_text("orig")

        class _Writer:
            def __init__(self, fname: str, content: str) -> None:
                self.fname = fname
                self.content = content

            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction:
                (target / self.fname).write_text(self.content)
                return FixAction(
                    observation_signature=observation.signature,
                    fixer_id=self.fname,
                    description=f"wrote {self.fname}",
                )

        def content_validate(t: Path) -> Assessment:
            a_fixed = (t / "a.txt").read_text() == "fixed"
            b_broken = (t / "b.txt").read_text() == "broken"
            score = 70 + (20 if a_fixed else 0) - (30 if b_broken else 0)
            obs: list[Observation] = []
            if not a_fixed:
                obs.append(_obs(category="structure", signature="good"))
            if not b_broken:
                obs.append(_obs(category="content", signature="bad"))
            return _assessment(score=score, verdict="block", observations=tuple(obs))

        good = FixerSpec("a.txt", "t", frozenset({"structure"}), _Writer("a.txt", "fixed"))
        bad = FixerSpec("b.txt", "t", frozenset({"content"}), _Writer("b.txt", "broken"))
        result = run_fix_loop(target=tmp_path, validate_fn=content_validate, fixers=[good, bad])

        assert a.read_text() == "fixed"
        assert b.read_text() == "orig"
        assert result.assessment_after.score == 90
        assert len(result.fixes_applied) == 1

    def test_validate_error_stops_gracefully(self, tmp_path: Path) -> None:
        """If validate raises mid-fix, the loop rolls back, stops gracefully
        keeping the best result so far, and records validation_error (#4)."""
        doc = tmp_path / "doc.txt"
        doc.write_text("original")
        calls = [0]

        def raising_validate(t: Path) -> Assessment:
            calls[0] += 1
            if calls[0] == 1:
                return _assessment(score=70, verdict="block", observations=(_obs(signature="s"),))
            raise RuntimeError("validate boom")

        class _Mutator:
            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction:
                (target / "doc.txt").write_text("mutated")
                return FixAction(observation.signature, "m", "mutated")

        spec = FixerSpec("m", "t", frozenset({"structure"}), _Mutator())
        result = run_fix_loop(target=tmp_path, validate_fn=raising_validate, fixers=[spec])

        assert doc.read_text() == "original"
        assert result.termination_reason == "validation_error"
        assert result.assessment_after.score == 70

    def test_reclassifies_reason_when_clean_at_max(self, tmp_path: Path) -> None:
        # With max_iterations=1, a fix that cleans the target reports "clean",
        # not "max_iterations" (#5).
        dirty = _assessment(score=70, verdict="block", observations=(_obs(signature="s"),))
        clean = _assessment(score=100, verdict="pass")
        seq = [dirty, clean]

        def validate(t: Path) -> Assessment:
            return seq.pop(0) if seq else clean

        spec = FixerSpec("f1", "t", frozenset({"structure"}), _CountingFixer())
        result = run_fix_loop(
            target=tmp_path, validate_fn=validate, fixers=[spec], max_iterations=1
        )
        assert result.termination_reason == "clean"
        assert result.assessment_after.score == 100

    def test_skips_stale_fixer_after_sibling_resolves_it(self, tmp_path: Path) -> None:
        # When one kept fix resolves several findings, the now-stale fixer for an
        # already-resolved finding is not run again in the same pass (#4).
        class _ResolveAll:
            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction:
                (target / "flag").write_text("done")
                return FixAction(observation.signature, "resolver", "resolved all")

        b_fixer = _CountingFixer()

        def validate(t: Path) -> Assessment:
            if (t / "flag").exists():
                return _assessment(score=100, verdict="pass")
            return _assessment(
                score=70,
                verdict="block",
                observations=(
                    _obs(category="structure", signature="A"),
                    _obs(category="content", signature="B"),
                ),
            )

        good = FixerSpec("resolver", "t", frozenset({"structure"}), _ResolveAll())
        bad = FixerSpec("counting", "t", frozenset({"content"}), b_fixer)
        result = run_fix_loop(target=tmp_path, validate_fn=validate, fixers=[good, bad])

        assert b_fixer.fix_count == 0
        assert result.assessment_after.score == 100

    def test_dispositioned_finding_is_not_fixed(self, tmp_path: Path) -> None:
        # A finding a human dispositioned (false-positive, accepted-risk,
        # deferred) is shown but not auto-fixed, even if a fixer matches it.
        obs = Observation(
            severity="high",
            category="structure",
            location=None,
            message="settled finding",
            fixable=False,
            signature="s",
            disposition="accepted-risk",
        )
        dirty = _assessment(score=70, verdict="block", observations=(obs,))
        fixer = _CountingFixer()
        spec = FixerSpec(
            fixer_id="f1",
            description="test",
            categories=frozenset({"structure"}),
            fixer=fixer,
        )
        result = run_fix_loop(target=tmp_path, validate_fn=lambda t: dirty, fixers=[spec])
        assert fixer.fix_count == 0
        assert result.fixes_applied == ()

    def test_restore_does_not_write_through_symlink(self, tmp_path: Path) -> None:
        """Rollback must not follow a destination symlink and corrupt a file
        outside the target (#4)."""
        outside = tmp_path / "outside.txt"
        outside.write_text("external")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "f.txt").write_text("orig")

        class _SymlinkFixer:
            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction:
                f = target / "f.txt"
                f.unlink()
                f.symlink_to(outside)
                return FixAction(
                    observation_signature=observation.signature,
                    fixer_id="sym",
                    description="symlinked",
                )

        def regressing_validate(t: Path) -> Assessment:
            f = t / "f.txt"
            is_orig = (not f.is_symlink()) and f.is_file() and f.read_text() == "orig"
            return _assessment(
                score=70 if is_orig else 50,
                verdict="block",
                observations=(_obs(signature="s"),),
            )

        spec = FixerSpec("sym", "t", frozenset({"structure"}), _SymlinkFixer())
        run_fix_loop(target=repo, validate_fn=regressing_validate, fixers=[spec])

        assert outside.read_text() == "external"
        assert (repo / "f.txt").read_text() == "orig"

    def test_restores_after_fixer_deletes_target(self, tmp_path: Path) -> None:
        """Rollback must survive a fixer that deletes the target directory (#5)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "f.txt").write_text("orig")

        class _NukeFixer:
            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction:
                shutil.rmtree(target)
                return FixAction(
                    observation_signature=observation.signature,
                    fixer_id="nuke",
                    description="nuked",
                )

        def regressing_validate(t: Path) -> Assessment:
            ok = (t / "f.txt").exists() and (t / "f.txt").read_text() == "orig"
            return _assessment(
                score=70 if ok else 50,
                verdict="block",
                observations=(_obs(signature="s"),),
            )

        spec = FixerSpec("nuke", "t", frozenset({"structure"}), _NukeFixer())
        run_fix_loop(target=repo, validate_fn=regressing_validate, fixers=[spec])

        assert (repo / "f.txt").read_text() == "orig"

    def test_dry_run_skips_when_fix_returns_none(self, tmp_path: Path) -> None:
        """Dry-run must not report fixes for observations the fixer rejects."""
        obs = _obs()
        dirty = _assessment(observations=(obs,))

        class _SelectiveFixer:
            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction | None:
                return None

        spec = FixerSpec(
            fixer_id="f1",
            description="selective",
            categories=frozenset({"structure"}),
            fixer=_SelectiveFixer(),
        )
        result = run_fix_loop(
            target=tmp_path,
            validate_fn=lambda t: dirty,
            fixers=[spec],
            dry_run=True,
        )
        assert result.fixes_applied == ()
        assert result.assessment_before is result.assessment_after

    def test_dry_run_restores_modified_files(self, tmp_path: Path) -> None:
        """Dry-run must not leave file modifications behind."""
        test_file = tmp_path / "content" / "test.adoc"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("original content\n")

        obs = Observation(
            severity="high",
            category="content",
            location="content/test.adoc:1",
            message="AI review comment found",
            fixable=False,
            signature="sig-restore",
        )
        dirty = _assessment(observations=(obs,))

        class _WritingFixer:
            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction | None:
                path = target / "content" / "test.adoc"
                path.write_text("MODIFIED\n")
                return FixAction(
                    observation_signature=observation.signature,
                    fixer_id="writer",
                    description="modified file",
                    files_modified=("content/test.adoc",),
                )

        spec = FixerSpec(
            fixer_id="writer",
            description="writes",
            categories=frozenset({"content"}),
            fixer=_WritingFixer(),
        )
        result = run_fix_loop(
            target=tmp_path,
            validate_fn=lambda t: dirty,
            fixers=[spec],
            dry_run=True,
        )
        assert len(result.fixes_applied) == 1
        assert "[dry-run]" in result.fixes_applied[0].description
        assert test_file.read_text() == "original content\n"

    def test_stops_when_fixer_returns_none(self, tmp_path: Path) -> None:
        obs = _obs()
        dirty = _assessment(observations=(obs,))

        class _NopFixer:
            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction | None:
                return None

        spec = FixerSpec(
            fixer_id="f1",
            description="nop",
            categories=frozenset({"structure"}),
            fixer=_NopFixer(),
        )
        result = run_fix_loop(
            target=tmp_path,
            validate_fn=lambda t: dirty,
            fixers=[spec],
        )
        assert result.fixes_applied == ()
        assert result.iterations == 1

    def test_rolls_back_files_when_fix_regresses(self, tmp_path: Path) -> None:
        """A regressing fix is undone on disk, not just dropped from the result.

        The returned assessment and the files on disk must agree: if the fix is
        discarded, the file it changed is restored to its pre-fix content.
        """
        doc = tmp_path / "doc.txt"
        doc.write_text("original")

        class _BreakingFixer:
            def can_fix(self, observation: Observation) -> bool:
                return True

            def fix(self, target: Path, observation: Observation) -> FixAction:
                (target / "doc.txt").write_text("broken")
                return FixAction(
                    observation_signature=observation.signature,
                    fixer_id="breaker",
                    description="broke it",
                )

        def content_validate(t: Path) -> Assessment:
            ok = (t / "doc.txt").read_text() == "original"
            return _assessment(
                score=70 if ok else 50,
                verdict="block",
                observations=(_obs(signature="s"),),
            )

        spec = FixerSpec(
            fixer_id="b",
            description="test",
            categories=frozenset({"structure"}),
            fixer=_BreakingFixer(),
        )
        result = run_fix_loop(target=tmp_path, validate_fn=content_validate, fixers=[spec])

        assert doc.read_text() == "original"
        assert result.assessment_after.score == 70
        assert result.fixes_applied == ()

    def test_never_returns_worse_than_before(self, tmp_path: Path) -> None:
        """A fix that lowers the score is rolled back, not kept (WS3 / R2-1).

        The loop applies a fix, the re-validate comes back worse; the loop must
        not return that worse assessment and must not report the fix as applied.
        """
        before = _assessment(
            score=70, verdict="block", observations=(_obs(signature="sig-fixable"),)
        )
        worse = _assessment(
            score=50,
            verdict="block",
            observations=(_obs(category="other", signature="sig-worse"),),
        )
        seq = [before, worse]

        def mock_validate(t: Path) -> Assessment:
            return seq.pop(0) if seq else worse

        spec = FixerSpec(
            fixer_id="f1",
            description="regressor",
            categories=frozenset({"structure"}),
            fixer=_CountingFixer(),
        )
        result = run_fix_loop(target=tmp_path, validate_fn=mock_validate, fixers=[spec])

        assert result.assessment_after.score >= result.assessment_before.score
        assert result.fixes_applied == ()
