"""Bounded command execution with retained stdout and stderr."""

from __future__ import annotations

import contextlib
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

from verdx._content import file_digest
from verdx.acceptance.models import Artifact, Execution, ExecutionState


def describe_artifact(path: Path, root: Path) -> Artifact:
    return Artifact(
        path=path.relative_to(root).as_posix(),
        sha256=file_digest(path),
        size=path.stat().st_size,
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError):
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()


def run_command(
    command: list[str],
    *,
    target: Path,
    root: Path,
    output_dir: Path,
    timeout: int,
    max_output_bytes: int,
) -> Execution:
    """Run an argument list without a shell and keep bounded output artifacts."""
    output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    stdout_path, stderr_path = output_dir / "stdout.log", output_dir / "stderr.log"
    started = time.monotonic()
    state: ExecutionState = "completed"
    error = None
    returncode = None
    process = None
    total = 0
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        try:
            process = subprocess.Popen(
                command,
                cwd=target,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
            assert process.stdout is not None and process.stderr is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, stdout)
                selector.register(process.stderr, selectors.EVENT_READ, stderr)
                while selector.get_map():
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        state, error = "timeout", f"command exceeded {timeout} seconds"
                        break
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        accepted = chunk[: max(0, max_output_bytes - total)]
                        key.data.write(accepted)
                        total += len(chunk)
                        if total > max_output_bytes:
                            state, error = "output_limit", "command exceeded the output limit"
                            break
                    if state != "completed":
                        break
            if state == "completed":
                try:
                    returncode = process.wait(
                        timeout=max(0.01, timeout - (time.monotonic() - started))
                    )
                except subprocess.TimeoutExpired:
                    state, error = "timeout", f"command exceeded {timeout} seconds"
        except KeyboardInterrupt:
            state, error = "interrupted", "run interrupted"
        except OSError as exc:
            state, error = "error", f"could not execute {command[0]}: {exc}"
        finally:
            if process is not None:
                if state != "completed":
                    _stop(process)
                process.wait()
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
    return Execution(
        state=state,
        returncode=returncode,
        error=error,
        stdout=describe_artifact(stdout_path, root),
        stderr=describe_artifact(stderr_path, root),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
