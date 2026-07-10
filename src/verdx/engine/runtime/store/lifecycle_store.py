"""local_jsonl backend — append-only JSONL event log with artifact files.

File structure under .agentic/ in the repo root:

    .agentic/
    ├── state.jsonl                    # Append-only event log
    ├── state.lock                     # Advisory lock for concurrent appends
    └── artifacts/
        └── {intent_id}/
            └── {artifact_type}.json   # Gate artifact files
"""

import json
import re
from pathlib import Path
from typing import Any

from verdx.contracts.errors import StoreError
from verdx.contracts.events import StateEvent
from verdx.engine.lifecycle.reducer import IntentState, apply
from verdx.engine.runtime.store.atomic import (
    atomic_append_line,
    atomic_write_text,
    exclusive_file_lock,
)

AGENTIC_DIR = ".agentic"
STATE_FILE = "state.jsonl"
LOCK_FILE = "state.lock"
ARTIFACTS_DIR = "artifacts"


class LocalJsonlBackend:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._dir = root / AGENTIC_DIR
        self._state_file = self._dir / STATE_FILE
        self._lock_file = self._dir / LOCK_FILE
        self._artifacts_dir = self._dir / ARTIFACTS_DIR

    @property
    def root(self) -> Path:
        return self._root

    @property
    def initialized(self) -> bool:
        return self._dir.is_dir()

    def init(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._artifacts_dir.mkdir(exist_ok=True)
        if not self._state_file.exists():
            self._state_file.touch()

    def append_event(self, event: StateEvent) -> None:
        if not self.initialized:
            raise StoreError(f"Not initialized: {self._dir} does not exist")
        line = event.model_dump_json()
        with exclusive_file_lock(self._lock_file):
            atomic_append_line(self._state_file, line)

    _SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")

    def record_artifact(self, intent_id: str, artifact_type: str, data: dict[str, Any]) -> str:
        if not self.initialized:
            raise StoreError(f"Not initialized: {self._dir} does not exist")
        if not self._SAFE_NAME.match(intent_id):
            raise StoreError(f"Invalid intent_id: {intent_id!r}")
        if not self._SAFE_NAME.match(artifact_type):
            raise StoreError(f"Invalid artifact_type: {artifact_type!r}")
        artifact_dir = self._artifacts_dir / intent_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{artifact_type}.json"
        atomic_write_text(artifact_path, json.dumps(data, indent=2) + "\n")
        return f"{intent_id}/{artifact_type}.json"

    def load_events(self, intent_id: str | None = None) -> list[StateEvent]:
        if not self._state_file.exists():
            return []
        events: list[StateEvent] = []
        for i, line in enumerate(self._state_file.read_text().strip().splitlines()):
            if not line.strip():
                continue
            try:
                event = StateEvent.model_validate_json(line)
            except Exception as exc:
                raise StoreError(f"Invalid event on line {i + 1}: {exc}") from exc
            if intent_id is None or event.intent_id == intent_id:
                events.append(event)
        return events

    def load_artifact(self, intent_id: str, artifact_type: str) -> dict[str, Any] | None:
        if not self._SAFE_NAME.match(intent_id):
            raise StoreError(f"Invalid intent_id: {intent_id!r}")
        if not self._SAFE_NAME.match(artifact_type):
            raise StoreError(f"Invalid artifact_type: {artifact_type!r}")
        path = self._artifacts_dir / intent_id / f"{artifact_type}.json"
        if not path.exists():
            return None
        result: dict[str, Any] = json.loads(path.read_text())
        return result

    def query_state(self, intent_id: str) -> IntentState | None:
        events = self.load_events(intent_id)
        if not events:
            return None
        state = IntentState(intent_id=intent_id)
        for event in events:
            apply(state, event)
        return state

    def list_intents(self) -> list[str]:
        events = self.load_events()
        seen: dict[str, None] = {}
        for event in events:
            seen.setdefault(event.intent_id, None)
        return list(seen)
