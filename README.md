# verdx

Verdx checks whether work meets a project's acceptance rules. It runs the checks
you name, records what happened, and returns `pass`, `fail`, `incomplete`, or
`not_applicable`. Required checks decide acceptance. Advisory checks stay visible.

![CI](https://github.com/ostinato-forge/verdx/actions/workflows/ci.yml/badge.svg?branch=main)

The project includes a Python engine, a command-line tool, and Markdown plugins.
It is an early implementation installed from source. It has not been published
as a package.

## Install and check this repository

Python 3.13 or later is required. Command execution is tested on Linux.

```bash
git clone https://github.com/ostinato-forge/verdx.git
cd verdx
uv sync --locked --extra dev
uv run verdx check . --output text
```

The included [verdx.toml](verdx.toml) runs lint, formatting, type checks, tests,
and documentation checks. Each attempt saves its policy, input file digests,
checker output, and decision under `.verdx/runs/<run-id>/`.
The CI test job runs this policy. Local receipts remain available until you
remove them; CI receipts remain on the runner for that job only.
The [acceptance example](examples/acceptance/README.md) demonstrates failure,
repair, recheck, and replay using a small local project.

## Add acceptance rules to a project

Create `verdx.toml` in the project directory:

```toml
version = 1
include = ["src/**", "tests/**", "README.md", "pyproject.toml", "uv.lock", "verdx.toml"]

[[checks]]
id = "tests"
command = ["uv", "run", "--locked", "pytest", "-q"]
version_command = ["uv", "run", "--locked", "pytest", "--version"]
timeout = 300
not_applicable_codes = [5]

[[checks]]
id = "readme"
domain = "markdown"
target = "README.md"

[[checks]]
id = "style-advice"
command = ["uv", "run", "--locked", "ruff", "check", "src/"]
required = false
```

Then run:

```bash
uv run verdx check /path/to/project --output text
```

Commands are argument lists, executed from the project root without a shell.
They run with your account's permissions. Use a policy and commands you trust.
The runner does not install tools or treat command output as an instruction.

Checks are required unless `required = false`. Command exit code 0 means pass,
1 means failure, and any other code means incomplete execution by default.
`success_codes`, `failure_codes`, and `not_applicable_codes` declare a tool's
exit-code contract. These lists cannot overlap. Pytest uses 5 when it collects
no tests.

`paths = ["**/*.md"]` runs a check only when an input file matches. It controls
whether the check applies; it does not change the command's arguments.

`version_command` records a tool's reported version. If the probe fails, the
check is incomplete. If you omit it, no tool version is inferred. Include
lockfiles and checker source in the input scope when they affect the result.

## Outcomes and exit codes

| Outcome | Exit code | Meaning |
| --- | --- | --- |
| `pass` | 0 | Every required check completed and passed. |
| `fail` | 1 | A required check found a failure. Other coverage gaps remain visible. |
| `incomplete` | 2 | Required evidence is missing, a command could not finish, or inputs changed. |
| `not_applicable` | 3 | No required check evaluated applicable work. |

Coverage describes required checks separately from their results. A failed test
is complete evidence of failure. A timed-out test is incomplete evidence. If some
required checks pass and another reports no tests or no matching files, the
result is incomplete.
A domain can report a failure and incomplete coverage together. Both remain
visible in the decision and in that check's result.

Acceptance checks do not average failures into a passing score. Any domain
finding above informational severity fails its check. A domain that could not
complete its checks is incomplete. Domain checks run with live checks disabled;
a skipped live check remains a coverage gap.

Output is JSON when piped and text in a terminal. `--output` selects either
format. Progress goes to stderr, so stdout remains parseable JSON.

## Replay saved evidence

Use the receipt path printed by `check`:

```bash
uv run verdx replay .verdx/runs/<run-id>/receipt.json --output text
```

Replay verifies saved artifact digests, reads the saved policy, and recomputes
the decision. It does not rerun commands, access the original target, or contact
network services. Copy the complete run directory to move the evidence elsewhere.
Replay returns the original decision's exit code. Replaying a failed run exits 1.

Each attempt gets its own directory. Later success leaves earlier failed and
incomplete attempts intact. Timeouts stop the command's process group on POSIX
systems and retain available output. An output limit stops the command and
records an incomplete result instead of silently discarding evidence.

The decision hash includes the policy, input manifests, runtime and checker
evidence, artifact digests, coverage, and verdict. Receipt run IDs, timestamps,
and measured command durations are excluded. Saved output remains part of its
artifact digest, including any timestamps a checker prints. Identical saved
evidence produces the same decision hash. A fresh run can produce a different
hash if its logs or environment differ.

Receipts are unsigned local records. Replay checks internal consistency. It does
not prove who produced a record or defend against someone rewriting both a
record and its hashes. Independent merge enforcement and signing belong in a
trusted CI integration, such as [Siglum](https://github.com/siglum-dev/siglum).

## What the input digest covers

`include` defaults to all source files. `exclude` removes explicit patterns.
File paths, executable flags, and all file bytes contribute to the manifest.
Hidden source files are included. Contents are streamed, including files larger
than 10 MiB. Changes between the initial and final snapshots make a run incomplete.

Known generated directories are excluded: `.git`, `.verdx`, `.venv`, `.tox`,
`.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.hypothesis`, `__pycache__`,
`node_modules`, `htmlcov`, `dist`, `build`, `.eggs`, and directories ending in
`.egg-info`. Python bytecode and `.coverage` files are also excluded. This is a
fixed list, not a complete implementation of `.gitignore`.

Acceptance checks reject symlinks in their declared input scope. Use regular
source files in that scope. The policy itself is always saved and checked for
changes, even when it is outside the input patterns.

A snapshot covers the declared local files. It does not freeze remote services,
the whole environment, or files a command reads outside that scope. A passing
command proves only the condition that command actually tests.

## Existing domain workflows

The `validate`, `fix`, and `drive` commands retain the scored domain interface:

```bash
uv run verdx validate README.md --domain markdown --no-flywheel
uv run verdx fix README.md --domain markdown --dry-run --no-flywheel
uv run verdx drive /path/to/target --fix-cmd "your-fix-command" --max-iterations 5
```

`validate` returns a scored `pass` or `block` assessment. Its profiles use score
thresholds, so a finding can coexist with a passing score. Use `check` when a
required rule must pass without findings and you need retained evidence.

`fix` applies registered mechanical repairs and revalidates. `drive` invokes an
external repair command and revalidates after each attempt. Both loops are bounded.

The learning store can annotate domain runs and hold approved overrides. It is
separate from acceptance receipts. Acceptance checks do not load its overrides.
Use `--no-flywheel` to disable enrichment in the existing domain commands.

## Build a domain plugin

| Domain | Checks |
| --- | --- |
| `markdown` | Heading order, relative links, and whitespace in Markdown files. |
| `example` | Trailing blank lines, as a small plugin template. |

Start with [the example domain](src/verdx/domains/example/__init__.py). Plugins
register through the `verdx.domains` entry-point group. They supply checks and
policy through `verdx.contracts`; they do not import the internal engine.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow.

## Compatibility and source history

Domain decision hashes now use `verdx.decision.v2`. Content hashes use full
SHA-256 digests over `verdx.content.v2` manifests. Old and new hashes are not
interchangeable. Existing cached identities will miss and checks will rerun.

Acceptance receipts use `verdx.acceptance.v1`. Unknown receipt versions are
rejected. See [PROVENANCE.md](PROVENANCE.md) for known source history and the
remaining extraction-history gap.

## License

Apache-2.0. See [LICENSE](LICENSE).
