"""Diagnostic event data model: secure run lifecycle logging.

DiagnosticEvent tracks progress through validation phases without
leaking sensitive content, prompts, tokens, or private actor details.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_REDACT_PATTERNS = (
    re.compile(r"(?:password|token|secret|api[_-]?key)\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"/home/\S+"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?:export\s+)?[A-Z_][A-Z0-9_]*=\S+"),
    re.compile(r"/(?:tmp|var|opt|root|etc)/\S+"),
)


def redact(value: str) -> str:
    """Replace sensitive patterns with [REDACTED]."""
    result = value
    for pattern in _REDACT_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    timestamp: str
    run_id: str
    phase: str
    validator_id: str | None
    status: str
    duration_ms: int | None
    message: str
    context: dict[str, str | int | bool]

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "phase": self.phase,
            "validator_id": self.validator_id,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "message": redact(self.message),
            "context": {
                k: redact(str(v)) if isinstance(v, str) else v for k, v in self.context.items()
            },
        }
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiagnosticEvent:
        return cls(
            timestamp=data["timestamp"],
            run_id=data["run_id"],
            phase=data["phase"],
            validator_id=data.get("validator_id"),
            status=data["status"],
            duration_ms=data.get("duration_ms"),
            message=data["message"],
            context=data.get("context", {}),
        )
