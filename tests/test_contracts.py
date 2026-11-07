"""Contract tests: architectural boundaries and public API stability.

Three layers:
1. Import boundaries — verdx never imports caller-side packages.
2. CLI surface contract — the expected commands with expected options.
3. Public API signatures — verdx.validate() and the engine API are stable.
4. Porcelain type contract — Assessment/Observation/Override have required fields.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from click.testing import CliRunner

from verdx.cli import main

_VERDX_SRC = Path(__file__).resolve().parent.parent / "src" / "verdx"

_FORBIDDEN_PACKAGES = frozenset(
    {
        "caller_marketplace",
        "caller_skills",
        "caller_runner",
        "caller_gate",
    }
)


def _collect_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_verdx_never_imports_forbidden_packages() -> None:
    violations: list[str] = []
    for py_file in sorted(_VERDX_SRC.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        imports = _collect_imports(source)
        for forbidden in _FORBIDDEN_PACKAGES & imports:
            rel = py_file.relative_to(_VERDX_SRC.parent.parent)
            violations.append(f"{rel} imports {forbidden}")
    assert not violations, "verdx must not import forbidden packages:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


class TestCLISurfaceContract:
    def test_cli_commands(self) -> None:
        assert set(main.commands.keys()) == {
            "check",
            "replay",
            "validate",
            "fix",
            "drive",
            "flywheel",
        }

    def test_validate_has_required_options(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--help"])
        assert result.exit_code == 0
        for opt in ("--static-only", "--changed-only", "--profile", "--domain", "--output"):
            assert opt in result.output, f"Missing option: {opt}"

    def test_fix_has_required_options(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["fix", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--static-only" in result.output


class TestAPISignatureContract:
    def test_validate_signature(self) -> None:
        from verdx.api import validate

        sig = inspect.signature(validate)
        params = set(sig.parameters.keys())
        required = {"target", "changed_only", "live", "profile", "domain"}
        missing = required - params
        assert not missing, f"verdx.validate() lost parameters: {missing}"

    def test_fix_signature(self) -> None:
        from verdx.api import fix

        sig = inspect.signature(fix)
        params = set(sig.parameters.keys())
        required = {"target", "dry_run", "live", "profile", "domain", "max_iterations"}
        missing = required - params
        assert not missing, f"verdx.fix() lost parameters: {missing}"

    def test_engine_validate_signature(self) -> None:
        from verdx.engine.api import validate as cg_validate

        sig = inspect.signature(cg_validate)
        params = set(sig.parameters.keys())
        required = {"target", "domains", "profile", "static_only", "threshold", "changed_files"}
        missing = required - params
        assert not missing, f"verdx.engine.api.validate() lost parameters: {missing}"


class TestPorcelainTypeContract:
    def test_assessment_has_required_fields(self) -> None:
        from verdx.types import Assessment

        fields = set(Assessment.__dataclass_fields__.keys())
        required = {
            "score",
            "verdict",
            "observations",
            "decision_hash",
            "overrides_applied",
            "validators_run",
            "metadata",
        }
        assert required <= fields

    def test_observation_has_required_fields(self) -> None:
        from verdx.types import Observation

        fields = set(Observation.__dataclass_fields__.keys())
        required = {"severity", "category", "location", "message", "fixable", "signature"}
        assert required <= fields

    def test_override_has_required_fields(self) -> None:
        from verdx.types import Override

        fields = set(Override.__dataclass_fields__.keys())
        required = {"signature", "decision", "justification", "by", "date", "expires"}
        assert required <= fields

    def test_fix_result_has_required_fields(self) -> None:
        from verdx.types import FixResult

        fields = set(FixResult.__dataclass_fields__.keys())
        required = {
            "assessment_before",
            "assessment_after",
            "fixes_applied",
            "iterations",
            "remaining_observations",
        }
        assert required <= fields

    def test_fix_action_has_required_fields(self) -> None:
        from verdx.types import FixAction

        fields = set(FixAction.__dataclass_fields__.keys())
        required = {"observation_signature", "fixer_id", "description", "files_modified"}
        assert required <= fields

    def test_flywheel_correction_has_required_fields(self) -> None:
        from verdx.engine.flywheel.types import Correction

        fields = set(Correction.__dataclass_fields__.keys())
        required = {
            "observation_signature",
            "target_key",
            "initial_severity",
            "final_decision",
            "description",
            "domain",
            "timestamp",
        }
        assert required <= fields

    def test_flywheel_pattern_has_required_fields(self) -> None:
        from verdx.engine.flywheel.types import Pattern

        fields = set(Pattern.__dataclass_fields__.keys())
        required = {
            "id",
            "symptoms",
            "diagnosis",
            "resolution",
            "category",
            "domain",
            "seen_count",
        }
        assert required <= fields

    def test_flywheel_annotation_has_required_fields(self) -> None:
        from verdx.engine.flywheel.types import Annotation

        fields = set(Annotation.__dataclass_fields__.keys())
        required = {
            "observation_signature",
            "pattern_id",
            "match_score",
            "suggested_decision",
            "confidence",
            "rationale",
        }
        assert required <= fields

    def test_public_exports(self) -> None:
        import verdx

        assert hasattr(verdx, "validate")
        assert hasattr(verdx, "fix")
        assert hasattr(verdx, "Assessment")
        assert hasattr(verdx, "Observation")
        assert hasattr(verdx, "Override")
        assert hasattr(verdx, "FixResult")
        assert hasattr(verdx, "FixAction")
