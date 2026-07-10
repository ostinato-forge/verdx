# verdx

![CI](https://github.com/ostinato-forge/verdx/actions/workflows/ci.yml/badge.svg?branch=main)

Deterministic verification engine with pluggable domain gates. You point it at a target, it runs the gate's checks, and it returns a scored verdict: pass or block. The same input produces the same verdict every time.

## What it does

verdx separates two jobs that are usually tangled together. Checks decide what to look for. The engine decides what the findings mean. A gate contributes checks and a scoring policy; the engine runs the checks, scores the findings, and produces one verdict with a decision hash. That hash is a fingerprint of the assessment, so two runs that reach the same verdict on the same content are provably the same decision.

Three things make up a run:

- **Scored verdict.** Every finding carries a severity and a set of dimensions. The engine scores them against the gate's policy and emits a single verdict (`pass` or `block`) plus a numeric score. Nothing in the verdict path reaches out to a network service, so the verdict is reproducible.
- **Decision hash.** The assessment is hashed into a short, stable identifier. Rerun on unchanged content and you get the same hash. Change the content and the hash changes with it.
- **Bounded auto-fix.** For findings that have a mechanical repair, a gate can register a fixer. verdx applies fixers in a bounded loop and re-validates after each pass. It never treats a target as fixed on its own say-so; the verdict decides.

## Install

```bash
uv sync
```

## Commands

verdx has three commands.

### validate

Run the gate against one or more targets and print the verdict.

```bash
# Validate a target (static plus live checks)
verdx validate /path/to/target

# Static checks only, no live checks
verdx validate /path/to/target --static-only

# JSON output (auto-selected when piped)
verdx validate /path/to/target --output json
```

The exit code is `0` when every target passes and `1` when any target is blocked.

### fix

Apply the gate's registered fixers to a target, then re-validate.

```bash
# Apply mechanical fixes in place
verdx fix /path/to/target

# Show what would change without touching files
verdx fix /path/to/target --dry-run
```

### drive

Hold the loop while a fix command you supply repairs the target. verdx validates, and while the verdict is `block` it hands the findings to your fix command on stdin, then re-validates. It exits `0` only when the verdict reaches `pass`. The loop is bounded by `--max-iterations`.

```bash
verdx drive /path/to/target --fix-cmd "your-fix-command" --max-iterations 5
```

The fix command never decides that the work is done. verdx re-validates after every pass and the verdict is the only thing that ends the loop.

## Example gates

Two gates ship with verdx.

| Gate | Checks |
| --- | --- |
| `markdown` | Heading structure, link validity, and style conventions in Markdown files. |
| `example` | A single check (trailing blank lines), kept deliberately small as a copy-me template. |

Gates are discovered through the `verdx.domains` entry-point group, so an installed gate is available without any extra wiring.

## Build your own gate

`src/verdx/domains/example/` is the template. It is three files (`__init__.py`, `validators.py`, `fixers.py`) with every step commented, and it is the smallest thing the engine will accept as a gate.

The shape of a gate:

1. **Write checks.** A check is a plain function decorated with `@simple_validator` that takes a target path and returns a list of findings. See `src/verdx/domains/example/validators.py`.
2. **Register the gate.** A single `register_domain()` function returns a `DomainSpec` describing the gate's name, its checks, its scoring policy, and how it discovers subjects. See `src/verdx/domains/example/__init__.py`.
3. **Optionally add fixers.** A fixer takes a finding and repairs it mechanically. See `src/verdx/domains/example/fixers.py`.
4. **Point the entry point at it.** Add your gate to the `verdx.domains` group in `pyproject.toml`.

Gate authors build against `verdx.contracts`, the stable public surface. Gates never import from `verdx.engine`, which is internal.

The `markdown` gate under `src/verdx/domains/markdown/` is a fuller worked example: three validators split across files, a config package with profiles, and fixers.

## Flywheel

verdx keeps a learning layer, the flywheel, that records how findings were resolved so it can annotate later runs with that context. It never enters the verdict path; it only enriches. Flywheel data lives in a separate data directory, discovered next to the repo or set with `VERDX_FLYWHEEL_DATA`, and it falls back to a local directory when no shared one is present.

```bash
verdx flywheel list         # list stored patterns
verdx flywheel sync         # commit and push flywheel data
```

Disable it for a single run with `--no-flywheel`.

## License

Apache-2.0.
