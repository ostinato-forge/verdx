"""Handler dispatch — manifest loading and subprocess execution.

This is an I/O module (like store/). It reads YAML config and calls
external processes. core/ never imports from here.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator

from verdx.contracts.artifacts import ArtifactType
from verdx.contracts.errors import FourEyesError

MANIFEST_FILENAME = "handlers.yml"


class DispatchError(FourEyesError):
    pass


class HandlerEntry(BaseModel):
    id: str
    version: str
    command: list[str]
    capabilities: list[str] = []
    stage: str
    output_artifact: str
    args_template: list[str] = []

    @field_validator("output_artifact")
    @classmethod
    def _validate_artifact_type(cls, v: str) -> str:
        valid = {a.value for a in ArtifactType}
        if v not in valid:
            raise ValueError(
                f"Unknown artifact type '{v}'. Must be one of: {', '.join(sorted(valid))}"
            )
        return v


class HandlerManifest(BaseModel):
    schema_version: str
    handlers: list[HandlerEntry]


def load_manifest(root: Path) -> HandlerManifest | None:
    path = root / ".agentic" / MANIFEST_FILENAME
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text())
    return HandlerManifest.model_validate(raw)


def find_handler(manifest: HandlerManifest, stage: str) -> HandlerEntry | None:
    for handler in manifest.handlers:
        if handler.stage == stage:
            return handler
    return None


def run_handler(
    handler: HandlerEntry,
    target: str,
    *,
    timeout: int = 300,
) -> dict[str, Any]:
    args = [a.format(target=target) for a in handler.args_template]
    cmd = handler.command + args

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DispatchError(f"Handler command not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DispatchError(f"Handler '{handler.id}' timed out after {timeout}s") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise DispatchError(
            f"Handler '{handler.id}' exited with code {result.returncode}: {detail}"
        )

    try:
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed
    except json.JSONDecodeError as exc:
        raise DispatchError(f"Handler '{handler.id}' produced invalid JSON output: {exc}") from exc
