"""markdown-gate: example domain gate for Markdown documentation QA."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verdx.contracts.domain import DomainSpec

__version__ = "1.0.0"


def register_domain() -> DomainSpec:
    """Entry-point factory for the ``verdx.domains`` group."""
    from verdx.contracts.check import CheckSpec, check_from_validator
    from verdx.contracts.domain import DomainSpec, SubjectDiscoveryResult
    from verdx.domains.markdown.config.defaults import load_default_markdown
    from verdx.domains.markdown.config.profiles import load_profiles
    from verdx.domains.markdown.validators import get_validators

    def _load_checks() -> tuple[CheckSpec, ...]:
        return tuple(check_from_validator(v, cfg, domain="markdown") for v, cfg in get_validators())

    return DomainSpec(
        name="markdown",
        markers=("README.md",),
        load_default_config=load_default_markdown,
        load_checks=_load_checks,
        load_profiles=load_profiles,
        discover_subjects=lambda path, key: SubjectDiscoveryResult(),
    )
