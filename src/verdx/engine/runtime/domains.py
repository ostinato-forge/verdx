"""Validation domain registry with entry-point discovery.

Domains register themselves via ``verdx.domains`` entry points.
Each entry point must be a callable that returns a
``verdx.contracts.domain.DomainSpec``.

This keeps the engine domain-agnostic: it never imports a domain
package directly.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from pathlib import Path

from verdx.contracts.domain import DomainSpec

_log = logging.getLogger(__name__)


class UnknownDomainError(ValueError):
    """Raised when a requested validation domain is not registered."""


# ---------------------------------------------------------------------------
# Entry-point discovery (cached at module level)
# ---------------------------------------------------------------------------

_discovered: dict[str, DomainSpec] | None = None
_load_errors: dict[str, str] | None = None


def _discover() -> dict[str, DomainSpec]:
    """Load all ``verdx.domains`` entry points exactly once.

    Each entry point must be a callable that returns a ``DomainSpec``.

    Broken entry points are logged and skipped -- one bad domain
    plugin must not break the whole CLI. Failures are tracked in
    ``_load_errors`` and exposed via ``domain_load_errors()``.

    Result is cached in ``_discovered`` for the process lifetime.
    """
    global _discovered, _load_errors
    if _discovered is not None:
        return _discovered

    registry: dict[str, DomainSpec] = {}
    errors: dict[str, str] = {}
    eps = entry_points(group="verdx.domains")
    for ep in eps:
        try:
            factory = ep.load()
            spec = factory()
        except Exception as exc:
            if ep.name not in registry:
                errors[ep.name] = f"{type(exc).__name__}: {exc}"
            continue

        if isinstance(spec, DomainSpec):
            # If another entry point with the same public domain name failed
            # earlier, a later successful provider should make that domain
            # usable rather than permanently "failed" for this process.
            errors.pop(ep.name, None)
            errors.pop(spec.name, None)
            registry[spec.name] = spec
        else:
            if ep.name not in registry:
                errors[ep.name] = f"returned {type(spec).__name__}, expected DomainSpec"
            continue

    for name, error in sorted(errors.items()):
        _log.warning("failed to load domain entry point %r: %s", name, error)

    _discovered = registry
    _load_errors = errors
    return _discovered


def _reset_discovery_cache() -> None:
    """Clear the cached discovery result (for testing only)."""
    global _discovered, _load_errors
    _discovered = None
    _load_errors = None


def domain_load_errors() -> dict[str, str]:
    """Return a mapping of entry-point names to their load error messages.

    Empty dict means all discovered domains loaded successfully.
    Call after ``_discover()`` (any public API triggers it).
    """
    _discover()
    return dict(_load_errors or {})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_domains() -> tuple[str, ...]:
    """Return registered domain names in stable sorted order."""
    return tuple(sorted(_discover()))


def get_domain(name: str) -> DomainSpec:
    """Resolve a validation domain by name.

    Returns the contracts.domain.DomainSpec for the domain.
    Raises ``UnknownDomainError`` if not found among discovered domains.
    If the domain's entry point failed to load, the error is included
    in the exception message.
    """
    registry = _discover()
    try:
        return registry[name]
    except KeyError as exc:
        errors = domain_load_errors()
        if name in errors:
            raise UnknownDomainError(f"domain {name!r} failed to load: {errors[name]}") from exc
        known = ", ".join(sorted(registry)) or "(none)"
        failed = ", ".join(sorted(errors)) if errors else ""
        msg = f"unknown validator domain {name!r}; known domains: {known}"
        if failed:
            msg += f"; failed to load: {failed}"
        raise UnknownDomainError(msg) from exc


# ---------------------------------------------------------------------------
# Domain auto-detection
# ---------------------------------------------------------------------------


def detect_domain(target: Path) -> str | None:
    """Auto-detect the validation domain for *target*.

    Queries discovered domains for their marker files.  Returns the
    first domain whose marker exists under *target*.  Returns ``None``
    when no heuristic matches (caller should require ``--domain``).

    When multiple domains match, the first in sorted-name order wins,
    making the result deterministic.
    """
    resolved = Path(target) if not isinstance(target, Path) else target
    if not resolved.is_dir():
        resolved = resolved.parent

    registry = _discover()
    for name in sorted(registry):
        spec = registry[name]
        for marker in spec.markers:
            if (resolved / marker).exists():
                return name
    return None


def detect_domains(target: Path) -> tuple[str, ...]:
    """Auto-detect ALL matching validation domains for *target*.

    Unlike ``detect_domain()`` which returns only the first match,
    this returns every domain whose marker exists under *target*,
    in sorted-name order.  Returns empty tuple when no match.

    Used by composite assessment to run all applicable domains.
    """
    resolved = Path(target) if not isinstance(target, Path) else target
    if not resolved.is_dir():
        resolved = resolved.parent

    registry = _discover()
    matched: list[str] = []
    for name in sorted(registry):
        spec = registry[name]
        for marker in spec.markers:
            if (resolved / marker).exists():
                matched.append(name)
                break
    return tuple(matched)
