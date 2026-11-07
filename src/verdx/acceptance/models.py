"""Strict inputs and saved evidence for project acceptance checks."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CheckRule(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    command: list[str] | None = None
    domain: str | None = None
    target: str = "."
    required: bool = True
    timeout: int = Field(default=300, ge=1, le=86400)
    paths: list[str] = Field(default_factory=list)
    version_command: list[str] | None = None
    success_codes: list[int] = Field(default_factory=lambda: [0])
    failure_codes: list[int] = Field(default_factory=lambda: [1])
    not_applicable_codes: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_rule(self) -> Self:
        if (self.command is None) == (self.domain is None):
            raise ValueError("each check needs exactly one command or domain")
        validate_patterns([self.target])
        if any(character in self.target for character in "*?["):
            raise ValueError("a domain target must be a path, not a pattern")
        if self.command is not None and self.target != ".":
            raise ValueError(
                "target applies only to domain checks; commands run at the project root"
            )
        for command in (self.command, self.version_command):
            if command is not None and (
                not command or any(not arg or "\0" in arg for arg in command)
            ):
                raise ValueError("commands must be non-empty argument lists without null bytes")
        if self.domain is not None:
            if not self.domain.strip() or self.version_command is not None:
                raise ValueError(
                    "domain checks need a domain name and use their own checker versions"
                )
            if self.success_codes != [0] or self.failure_codes != [1] or self.not_applicable_codes:
                raise ValueError("exit-code mappings apply only to command checks")
        codes = self.success_codes + self.failure_codes + self.not_applicable_codes
        if not self.success_codes or any(code < 0 or code > 255 for code in codes):
            raise ValueError("exit codes must be between 0 and 255, with at least one success code")
        if len(codes) != len(set(codes)):
            raise ValueError("exit-code mappings must not overlap or contain duplicates")
        validate_patterns(self.paths)
        return self


def validate_patterns(patterns: list[str]) -> None:
    for pattern in patterns:
        if not pattern or "\\" in pattern or PurePosixPath(pattern).is_absolute():
            raise ValueError("file patterns must be non-empty paths relative to the target")
        if ".." in PurePosixPath(pattern).parts:
            raise ValueError("file patterns must stay inside the target")


class Policy(Model):
    version: Literal[1] = 1
    include: list[str] = Field(default_factory=lambda: ["**"])
    exclude: list[str] = Field(default_factory=list)
    max_output_bytes: int = Field(default=10_485_760, ge=1024, le=104_857_600)
    checks: list[CheckRule] = Field(min_length=1)

    @model_validator(mode="after")
    def check_policy(self) -> Self:
        if not self.include:
            raise ValueError("include must name at least one input pattern")
        validate_patterns(self.include + self.exclude)
        ids = [check.id for check in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("check IDs must be unique")
        return self


class Artifact(Model):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)

    @model_validator(mode="after")
    def check_path(self) -> Self:
        path = PurePosixPath(self.path)
        if not self.path or path.is_absolute() or "\\" in self.path:
            raise ValueError("artifact paths must be relative")
        if any(part in (".", "..") for part in self.path.split("/")):
            raise ValueError("artifact paths must stay inside the evidence directory")
        return self


class InputFile(Model):
    path: str
    kind: Literal["file", "symlink"]
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    executable: bool

    @model_validator(mode="after")
    def check_path(self) -> Self:
        Artifact(path=self.path, sha256=self.sha256, size=self.size)
        return self


ExecutionState = Literal[
    "completed", "timeout", "error", "output_limit", "interrupted", "not_applicable", "not_run"
]


class Execution(Model):
    state: ExecutionState
    returncode: int | None = None
    error: str | None = None
    stdout: Artifact | None = None
    stderr: Artifact | None = None
    duration_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_execution(self) -> Self:
        if self.state == "completed" and (
            self.returncode is None or self.stdout is None or self.stderr is None
        ):
            raise ValueError("a completed command needs an exit code and both output artifacts")
        if self.state in ("not_applicable", "not_run") and self.returncode is not None:
            raise ValueError("an unexecuted check cannot have an exit code")
        return self


class CheckEvidence(Model):
    id: str
    execution: Execution
    version: Execution | None = None


CheckStatus = Literal["pass", "fail", "incomplete", "not_applicable"]
CheckCoverage = Literal["full", "incomplete", "none"]


class CheckDecision(Model):
    id: str
    required: bool
    status: CheckStatus
    coverage: CheckCoverage
    reason: str


class Decision(Model):
    outcome: CheckStatus
    coverage: CheckCoverage
    reasons: list[str]
    checks: list[CheckDecision]


class Receipt(Model):
    schema_version: Literal["verdx.acceptance.v1"]
    run_id: str
    started_at: str
    finished_at: str
    target: str
    runtime: dict[str, str]
    policy: Policy
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_artifact: Artifact
    before: list[InputFile]
    after: list[InputFile]
    input_error: str | None = None
    evidence: list[CheckEvidence]
    decision: Decision
    decision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
