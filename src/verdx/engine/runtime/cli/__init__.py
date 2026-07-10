"""verdx CLI — lifecycle gates and independent validation."""

from __future__ import annotations

import click

from verdx.engine import __version__
from verdx.engine.runtime.domains import get_domain as get_domain


class _DefaultValidateGroup(click.Group):
    """A Click group that routes bare paths to the ``validate`` subcommand.

    When the first positional argument is not a known subcommand *and*
    corresponds to an existing filesystem path, the group silently
    rewrites the argument list to ``["validate", <path>, ...]``.

    This gives users the simple UX::

        verdx /path/to/target
        verdx /path/to/target ch01s05
        verdx /path/to/target --static-only

    while keeping every explicit subcommand (``validate``, ``history``,
    ``init``, ``emit``, ...) intact.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        """Intercept argument parsing to detect implicit validate calls."""
        from pathlib import Path

        if not args or args[0].startswith("-"):
            return super().parse_args(ctx, args)

        first = args[0]

        if self.get_command(ctx, first) is not None:
            return super().parse_args(ctx, args)

        if Path(first).exists():
            args = ["validate", *args]

        return super().parse_args(ctx, args)


@click.group(cls=_DefaultValidateGroup)
@click.version_option(version=__version__, prog_name="verdx")
def main() -> None:
    """verdx — evidence-based lifecycle gates with independent validation."""


# Register command modules (side-effect imports).
from verdx.engine.runtime.cli import (  # noqa: E402, F401
    _actors,
    _audit,
    _dispositions,
    _domains,
    _history,
    _hooks,
    _lifecycle,
    _maintenance,
    _pipeline,
    _validation,
)

# Re-export private helpers that tests import directly.
from verdx.engine.runtime.cli._helpers import (  # noqa: E402, F401
    _MAX_HASH_FILE_SIZE,
    _compute_content_hash,
    _pipeline_evidence_summary_to_dict,
    _quick_profiles,
    _resolve_domain_for_targets,
)
