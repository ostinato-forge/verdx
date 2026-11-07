# verdx - development guide

## What this is

`verdx check` runs a project's required and advisory checks from `verdx.toml`.
It saves replayable evidence and returns pass, fail, incomplete, or not applicable.
`verdx replay` verifies that evidence without running commands. The existing
`validate`, `fix`, and `drive` commands use scored domain assessments.

## Layout

Everything lives under `src/verdx/`:

```
src/verdx/
├── api.py, cli.py          # Public API and CLI
├── acceptance/            # Project policy, command execution, receipts, replay
├── _content.py            # Shared full content digests and input manifests
├── types.py, translate.py  # Porcelain types, engine-to-porcelain translation
├── simple.py               # @simple_validator, @simple_fixer decorators
├── fixers.py, fix_loop.py  # Fixer discovery and bounded repair loop
├── _drive.py               # The drive loop (validate, fix, re-validate)
├── _flywheel_cli.py        # `verdx flywheel` subcommands
├── contracts/              # Stable public surface for gate authors
├── engine/                 # Core engine internals (not for external use)
│   ├── api.py              # Engine-level validate(), quick_check()
│   ├── config/             # Domain config loading
│   ├── flywheel/           # Learning layer: patterns, corrections, overrides
│   ├── runtime/            # Executor, planner, domain discovery, CLI, store
│   ├── validation/         # Scoring, escalation, coverage, canonicalization
│   └── lifecycle/          # State machine, gates, audit, transitions
└── domains/                # Domain gates (discovered via verdx.domains entry points)
    ├── example/            # Smallest possible gate; the copy-me template
    └── markdown/           # Fuller worked gate: 3 validators, config, fixers

tests/                      # Mirrors src/ structure
```

Gates are discovered through the `verdx.domains` entry-point group declared in `pyproject.toml`. The engine never imports a gate package directly.

Flywheel data (learned patterns, corrections, overrides) lives outside this repo, in a separate data directory. verdx discovers a sibling data directory next to the repo, or you set `VERDX_FLYWHEEL_DATA`. With neither present it falls back to a local directory.

### Import conventions

```python
# Gate authors import from contracts (stable public API)
from verdx.contracts import Severity, Confidence, ValidationContext, ValidatorResult

# Porcelain callers use the top-level API
from verdx.api import validate, fix, drive

# Engine internals (not for external use)
from verdx.engine.runtime.engine import run_validation
```

## Development

```bash
# Install in development mode
uv sync --locked --extra dev

# Run all tests
uv run python -m pytest tests/ -v

# Run tests for a specific area
uv run python -m pytest tests/contracts/ -v
uv run python -m pytest tests/engine/ -v
uv run python -m pytest tests/domains/ -v

# Lint and type check
uv run ruff check src/ tests/
uv run mypy --package verdx

# CLI
uv run verdx --help
uv run verdx validate <target>
uv run verdx fix <target> --dry-run
```

`make check` runs lint, typecheck, and tests together.
`make acceptance` runs those checks and documentation checks through Verdx,
with a retained receipt for each attempt. CI uses this acceptance policy.

## Conventions

- **Build system:** Hatchling
- **Python:** >=3.13 required
- **Source layout:** `src/verdx/` (single namespace, standard src layout)
- **Lint:** ruff (line-length 100, py313 target)
- **Type check:** mypy strict mode
- **Test:** pytest with --strict-markers; coverage floor is 60
- **Commits:** Conventional Commits (feat:, fix:, refactor:, test:, docs:, chore:)

## Key principle

**Derive the verdict from recorded evidence.** Acceptance checks distinguish
failure, missing evidence, and no applicable work. Required failures cannot be
averaged into a passing score. Replay uses only the saved policy and evidence;
it never executes commands. Keep earlier failed or incomplete attempts intact.

Acceptance does not load learning-store overrides. Domain assessment hashes use
`verdx.decision.v2`; source manifests use `verdx.content.v2`. Hash changes require
explicit versioning and compatibility tests. Gate authors build against
`verdx.contracts`; the engine is internal.
