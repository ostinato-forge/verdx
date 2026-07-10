"""example-gate: the smallest possible domain gate.

Copy the three files in this package (validators.py, fixers.py, __init__.py)
to start your own gate. The engine discovers gates via the ``verdx.domains``
entry-point group; registration is a single function (``register_domain``)
that returns a ``DomainSpec`` describing what your gate provides.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verdx.contracts.domain import DomainSpec

__version__ = "1.0.0"


def register_domain() -> DomainSpec:
    """Entry-point factory for the ``verdx.domains`` group.

    The engine calls this at startup to learn about the gate: its name, its
    checks, its config, and how to discover subjects. Keep all imports inside
    this function so the module itself is importable with zero side-effects.
    """
    # These are the stable public contracts that gate authors build against.
    # Never import from verdx.engine directly; the engine is an internal.
    from verdx.contracts.check import CheckSpec, check_from_validator

    # Config types needed to produce a minimal valid DomainConfig.
    from verdx.contracts.config import DomainConfig, EvidenceLimits
    from verdx.contracts.dimensions import DimLevel, EscalationLevel
    from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
    from verdx.contracts.escalation import (
        EscalationFloor,
        EscalationPolicy,
        EscalationRule,
        RuleMatch,
    )

    # Import the validators defined in this gate.
    from verdx.domains.example.validators import check_trailing_blank_lines

    def get_validators() -> tuple[tuple[object, dict[str, object]], ...]:
        """Return (validator_wrapper, config_dict) pairs for this gate.

        The validator wrapper (produced by @simple_validator) carries the
        raw function as .fn and has __module__/__qualname__ set to match
        the wrapped function, so check_from_validator can build the correct
        import path for the engine's PythonAdapter.
        """
        return ((check_trailing_blank_lines, {}),)

    def _load_checks() -> tuple[CheckSpec, ...]:
        """Build CheckSpec objects from the gate's validators.

        This is the one-liner that replaces hand-crafting CheckSpec dicts.
        check_from_validator reads the wrapper's metadata (id, version,
        category, module, qualname) and fills in the CheckSpec for you.
        """
        return tuple(check_from_validator(v, cfg, domain="example") for v, cfg in get_validators())

    def load_default_config() -> DomainConfig:
        """Return a minimal valid DomainConfig for the example gate.

        DomainConfig is the engine's contract for how findings are scored
        and escalated. The escalation policy below is the simplest policy
        that satisfies the contract: one floor and two rules (any-high
        escalates, all-low de-escalates). Copy from markdown and adjust
        the category/dimension names to match your own gate.
        """
        policy = EscalationPolicy(
            version=1,
            floors=(EscalationFloor(category="example", minimum=EscalationLevel.L1),),
            rules=(
                EscalationRule(
                    name="high-finding",
                    match=RuleMatch(quantifier="any", dimension="*", level=DimLevel.HIGH),
                    level=EscalationLevel.L3,
                ),
                EscalationRule(
                    name="fallback",
                    match=RuleMatch(quantifier="all", dimension="*", level=DimLevel.LOW),
                    level=EscalationLevel.L0,
                ),
            ),
        )
        return DomainConfig(
            version=1,
            policy=policy,
            critical_validators=frozenset(),
            dimensions=("example",),
            evidence_limits=EvidenceLimits(
                max_evidence_bytes=65536,
                max_string_length=4096,
                max_data_entries=50,
            ),
            timeout_seconds=300,
            dimension_map=None,
            scoring_config=None,
        )

    def load_profiles() -> tuple[dict[str, object], ...]:
        """Return available configuration profiles for the example gate.

        Profiles let users switch between configurations (e.g., strict/lax).
        Return an empty tuple if your gate has only one mode.
        """
        return ()

    # DomainSpec is the complete contract the engine reads. name= must match
    # the key you use in pyproject.toml's [project.entry-points."verdx.domains"].
    return DomainSpec(
        name="example",
        markers=(),
        load_default_config=load_default_config,
        load_checks=_load_checks,
        load_profiles=load_profiles,
        # discover_subjects is for stateful/subject-scoped workflows. For a
        # simple file-level gate, return an empty SubjectDiscoveryResult.
        discover_subjects=lambda path, key: SubjectDiscoveryResult(),
    )
