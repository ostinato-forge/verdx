"""Validator registry for the markdown gate."""

from __future__ import annotations

from typing import Any

from verdx.domains.markdown.validators.headings import check_headings
from verdx.domains.markdown.validators.links import check_links
from verdx.domains.markdown.validators.style import check_style


def get_validators() -> tuple[tuple[Any, dict[str, Any]], ...]:
    """Return (validator, config) pairs for the markdown gate.

    Each check function is decorated in place with ``@simple_validator``, so the
    module-level name IS the wrapper. ``check_from_validator`` reads the wrapper's
    ``__module__``/``__qualname__`` (adopted from the wrapped function) to build a
    PythonAdapter import path that resolves back to the wrapper. The engine calls
    ``wrapper(check_context)`` via that adapter.
    """
    return (
        (check_headings, {}),
        (check_links, {}),
        (check_style, {}),
    )
