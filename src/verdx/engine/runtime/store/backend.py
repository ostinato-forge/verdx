"""Backend Protocol — the swap interface for orchestration storage.

local_jsonl implements this now. A Temporal adapter would implement it later
without changing anything in egon.core/.
"""

from typing import Any, Protocol

from verdx.contracts.events import StateEvent
from verdx.engine.lifecycle.reducer import IntentState


class OrchestratorBackend(Protocol):
    def append_event(self, event: StateEvent) -> None:
        """Append a state event to the log. Must be crash-safe."""
        ...

    def record_artifact(self, intent_id: str, artifact_type: str, data: dict[str, Any]) -> str:
        """Write a gate artifact. Returns the artifact reference path."""
        ...

    def load_events(self, intent_id: str | None = None) -> list[StateEvent]:
        """Load all events, optionally filtered by intent_id."""
        ...

    def load_artifact(self, intent_id: str, artifact_type: str) -> dict[str, Any] | None:
        """Load a gate artifact by type. Returns None if not found."""
        ...

    def query_state(self, intent_id: str) -> IntentState | None:
        """Reconstruct current state for an intent via replay."""
        ...

    def list_intents(self) -> list[str]:
        """List all known intent IDs."""
        ...
