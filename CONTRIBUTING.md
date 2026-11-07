# Contributing to verdx

## Setup

```bash
git clone https://github.com/ostinato-forge/verdx.git
cd verdx
uv sync --locked --extra dev
```

Run the checks before you open a pull request:

```bash
make check          # lint, typecheck, and tests
```

Run the same checks through the acceptance workflow to retain their evidence:

```bash
uv run verdx check . --output text
```

This also checks the project documentation. Each attempt stays under
`.verdx/runs/`. Replay a receipt with `uv run verdx replay PATH/receipt.json`.
The default policy is [verdx.toml](verdx.toml).

Or individually:

```bash
uv run ruff check src/ tests/ examples/
uv run ruff format --check src/ tests/ examples/
uv run mypy --package verdx
uv run python -m pytest tests/ -q
```

The coverage floor is 60. New code should keep the suite green and add tests for the behavior it introduces.

## How verdx is structured

The acceptance runner executes the commands and domains named in `verdx.toml`.
Required checks decide acceptance. It saves the policy, input digests, output,
coverage, and result for offline replay.

The domain engine runs registered validators and scores their findings. Its
existing public API returns a scored assessment. The acceptance runner uses the
underlying findings and coverage directly. It does not use a score threshold to
accept a required domain check.

Gate authors work against `verdx.contracts`, the stable public surface. The engine under `verdx.engine` is internal and is not part of the contract; contract tests enforce that gates never import it.

## Writing a validator

A validator inspects a target and produces findings. The simplest form is a plain function decorated with `@simple_validator`:

```python
from pathlib import Path

from verdx.simple import simple_validator


@simple_validator(id="md-headings", version="1.0.0", category="structure")
def check_headings(target: Path) -> list[str]:
    # target is the directory being validated.
    issues = []
    # ... inspect files under target ...
    return issues
```

The decorator wraps the function into the full validator protocol the engine expects. Each string you return becomes a finding. The `category` maps to a scoring dimension declared in your gate's config.

## Adding a fixer

A fixer repairs a finding mechanically. It takes the target and an observation and returns a short description of what it changed, or `None` when it cannot help:

```python
from verdx.simple import simple_fixer


@simple_fixer(id="fix-headings", categories={"structure"})
def fix_headings(target, observation):
    if "some fixable condition" in observation.message:
        # ... apply the repair ...
        return "Fixed the heading"
    return None
```

verdx applies fixers in a bounded loop and re-validates after each pass. A fixer never decides that the target is done; the verdict does.

## Building a new gate

Copy `src/verdx/domains/example/`. It is the smallest thing the engine accepts as a gate: three files (`__init__.py`, `validators.py`, `fixers.py`), each step commented.

1. Write your checks in `validators.py`.
2. In `__init__.py`, write `register_domain()` returning a `DomainSpec` with your gate's name, checks, scoring policy, and subject discovery.
3. Add optional fixers in `fixers.py`.
4. Register the gate under the `verdx.domains` entry-point group in `pyproject.toml`.

The `markdown` gate under `src/verdx/domains/markdown/` is a fuller example: three validators split across files, a config package with profiles, and fixers. Look there when you need more than the minimal shape.

Add tests for your gate under `tests/domains/`. `tests/domains/example/` and `tests/domains/markdown/` show the pattern.

## Using verdx from code

```python
import verdx

assessment = verdx.validate("path/to/target", profile="standard")
if assessment.verdict == "block":
    fix_result = verdx.fix("path/to/target")
```

## Conventions

- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).
- **Style:** ruff formats and lints; mypy runs in strict mode. Keep both green.
- **Public surface:** if you change `verdx.contracts`, update the contract tests in the same change.

## Good first contributions

- Add a check to the `markdown` gate for a real Markdown problem, with edge-case tests.
- Improve an error message so a failing run tells the reader what to do next.
- Write a new gate for a file type you care about, starting from `example`.
