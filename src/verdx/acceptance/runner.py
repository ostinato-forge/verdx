"""Run a frozen acceptance policy and save one evidence directory per attempt."""

from __future__ import annotations

import platform
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from verdx import __version__
from verdx._content import content_manifest, matches
from verdx.acceptance.models import (
    CheckEvidence,
    CheckRule,
    Decision,
    Execution,
    InputFile,
    Policy,
    Receipt,
)
from verdx.acceptance.policy import load_policy, policy_hash
from verdx.acceptance.process import describe_artifact, run_command
from verdx.acceptance.receipt import decision_hash, derive_decision, replay
from verdx.engine.runtime.store.atomic import atomic_write_text


def _snapshot(target: Path, policy: Policy) -> list[InputFile]:
    records = content_manifest(target, include=tuple(policy.include), exclude=tuple(policy.exclude))
    return [InputFile.model_validate(record) for record in records]


def _git_head(target: Path) -> str:
    try:
        root = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if root.returncode != 0 or Path(root.stdout.strip()).resolve() != target:
            return "unavailable"
        result = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def _execute(
    rule: CheckRule,
    *,
    target: Path,
    root: Path,
    index: int,
    policy: Policy,
    before: list[InputFile],
) -> CheckEvidence:
    if rule.paths and not any(matches(file.path, tuple(rule.paths)) for file in before):
        return CheckEvidence(
            id=rule.id,
            execution=Execution(
                state="not_applicable",
                error="no input files match this check's paths",
            ),
        )
    output = root / "checks" / str(index)
    version = None
    if rule.version_command is not None:
        version = run_command(
            rule.version_command,
            target=target,
            root=root,
            output_dir=output / "version",
            timeout=min(rule.timeout, 30),
            max_output_bytes=policy.max_output_bytes,
        )
        if version.state != "completed" or version.returncode != 0:
            return CheckEvidence(
                id=rule.id,
                version=version,
                execution=Execution(
                    state="interrupted" if version.state == "interrupted" else "not_run",
                    error="checker version probe did not complete",
                ),
            )
    command = rule.command
    if rule.domain is not None:
        domain_target = (target / rule.target).resolve()
        if not domain_target.is_relative_to(target):
            return CheckEvidence(
                id=rule.id,
                execution=Execution(
                    state="error",
                    error="domain target leaves the project directory",
                ),
            )
        domain_files = content_manifest(domain_target) if domain_target.exists() else []
        domain_base = domain_target.parent if domain_target.is_file() else domain_target
        domain_paths = {
            (domain_base / item["path"]).relative_to(target).as_posix() for item in domain_files
        }
        if not domain_paths <= {item.path for item in before}:
            return CheckEvidence(
                id=rule.id,
                execution=Execution(
                    state="error",
                    error="domain target is outside the declared input scope",
                ),
            )
        command = [
            sys.executable,
            "-m",
            "verdx.acceptance.domain_worker",
            rule.domain,
            str(domain_target),
        ]
    assert command is not None
    execution = run_command(
        command,
        target=target,
        root=root,
        output_dir=output / "execution",
        timeout=rule.timeout,
        max_output_bytes=policy.max_output_bytes,
    )
    return CheckEvidence(id=rule.id, execution=execution, version=version)


def check(
    target: str | Path = ".",
    *,
    config: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[Receipt, Path]:
    """Run project checks and return the saved, replay-verified receipt."""
    target = Path(target).resolve()
    if not target.is_dir():
        raise ValueError("acceptance checks need a project directory")
    config_path = Path(config).resolve() if config is not None else target / "verdx.toml"
    policy, raw_policy = load_policy(config_path)
    started = datetime.now(UTC).isoformat()
    run_id = uuid.uuid4().hex
    state_dir = target / ".verdx"
    if state_dir.is_symlink() or (state_dir / "runs").is_symlink():
        raise ValueError("the evidence directory must not be a symlink")
    root = state_dir / "runs" / run_id
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    (root / "policy.toml").write_bytes(raw_policy)
    policy_artifact = describe_artifact(root / "policy.toml", root)
    before: list[InputFile] = []
    after: list[InputFile] = []
    input_error = None
    evidence: list[CheckEvidence] = []
    runtime = {
        "verdx": __version__,
        "python": platform.python_version(),
        "platform": platform.system(),
        "git_head": _git_head(target),
    }
    try:
        before = _snapshot(target, policy)
        if any(file.kind == "symlink" for file in before):
            input_error = "input scope includes symlinks; name regular source files in include"
    except (OSError, ValueError) as exc:
        input_error = f"could not snapshot input files: {exc}"
    except KeyboardInterrupt:
        input_error = "run interrupted before input snapshot completed"
    interrupted = False
    for index, rule in enumerate(policy.checks):
        if input_error or interrupted:
            result = CheckEvidence(
                id=rule.id,
                execution=Execution(
                    state="not_run",
                    error=input_error or "run interrupted",
                ),
            )
        else:
            try:
                if progress is not None:
                    progress(f"Running {rule.id}")
                result = _execute(
                    rule, target=target, root=root, index=index, policy=policy, before=before
                )
            except KeyboardInterrupt:
                result = CheckEvidence(
                    id=rule.id,
                    execution=Execution(
                        state="interrupted",
                        error="run interrupted",
                    ),
                )
            except (OSError, ValueError) as exc:
                result = CheckEvidence(
                    id=rule.id,
                    execution=Execution(
                        state="error",
                        error=f"could not complete check: {exc}",
                    ),
                )
            interrupted = result.execution.state == "interrupted"
        evidence.append(result)
    try:
        after = _snapshot(target, policy)
        if config_path.read_bytes() != raw_policy:
            input_error = "acceptance policy changed while checks were running"
        if _git_head(target) != runtime["git_head"]:
            input_error = "Git HEAD changed while checks were running"
    except (OSError, ValueError) as exc:
        input_error = f"could not verify input files after checks: {exc}"
    except KeyboardInterrupt:
        input_error = "run interrupted before final input snapshot completed"
    receipt = Receipt(
        schema_version="verdx.acceptance.v1",
        run_id=run_id,
        started_at=started,
        finished_at=datetime.now(UTC).isoformat(),
        target=str(target),
        runtime=runtime,
        policy=policy,
        policy_hash=policy_hash(policy),
        policy_artifact=policy_artifact,
        before=before,
        after=after,
        input_error=input_error,
        evidence=evidence,
        decision=Decision(outcome="incomplete", coverage="incomplete", reasons=[], checks=[]),
        decision_hash="0" * 64,
    )
    receipt = receipt.model_copy(update={"decision": derive_decision(receipt, root)})
    receipt = receipt.model_copy(update={"decision_hash": decision_hash(receipt)})
    path = root / "receipt.json"
    atomic_write_text(path, receipt.model_dump_json(indent=2) + "\n")
    return replay(path), path
