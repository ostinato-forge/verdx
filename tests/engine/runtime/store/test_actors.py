"""Tests for actor directory — minimal v1."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdx.engine.runtime.store.actors import ActorDirectory, ActorEntry

if TYPE_CHECKING:
    from pathlib import Path


def _entry(**overrides) -> ActorEntry:
    defaults = {
        "opaque_id": "opaque-001",
        "role": "content-author",
        "kind": "agent",
    }
    return ActorEntry(**(defaults | overrides))


class TestActorEntry:
    def test_round_trip(self):
        e = _entry(team="training")
        assert ActorEntry.from_dict(e.to_dict()) == e

    def test_optional_team(self):
        e = _entry()
        assert e.team is None
        d = e.to_dict()
        assert "team" not in d


class TestActorDirectory:
    def test_put_and_get(self, tmp_path: Path):
        d = ActorDirectory(tmp_path)
        d.put(_entry())
        result = d.get("opaque-001")
        assert result is not None
        assert result.role == "content-author"

    def test_get_nonexistent(self, tmp_path: Path):
        d = ActorDirectory(tmp_path)
        assert d.get("missing") is None

    def test_update_existing(self, tmp_path: Path):
        d = ActorDirectory(tmp_path)
        d.put(_entry(role="author"))
        d.put(_entry(role="reviewer"))
        result = d.get("opaque-001")
        assert result is not None
        assert result.role == "reviewer"

    def test_delete(self, tmp_path: Path):
        d = ActorDirectory(tmp_path)
        d.put(_entry())
        assert d.delete("opaque-001") is True
        assert d.get("opaque-001") is None

    def test_delete_nonexistent(self, tmp_path: Path):
        d = ActorDirectory(tmp_path)
        assert d.delete("missing") is False

    def test_list_all(self, tmp_path: Path):
        d = ActorDirectory(tmp_path)
        d.put(_entry(opaque_id="id-1", role="author"))
        d.put(_entry(opaque_id="id-2", role="reviewer"))
        entries = d.list_all()
        assert len(entries) == 2

    def test_list_empty(self, tmp_path: Path):
        d = ActorDirectory(tmp_path)
        assert d.list_all() == []

    def test_deletion_does_not_affect_observations(self, tmp_path: Path):
        """Deleting an actor entry leaves observations readable.

        This proves the core design invariant: observations reference
        actors by opaque ID, and the actor directory is optional metadata.
        If the directory entry is deleted, observations still contain
        the opaque_id and are fully functional.
        """
        d = ActorDirectory(tmp_path)
        d.put(_entry(opaque_id="opaque-author"))

        from tests.contracts.test_models import _make_observation
        from verdx.contracts.observation import Observation

        obs = _make_observation(producer_id="opaque-author")

        d.delete("opaque-author")
        assert d.get("opaque-author") is None

        assert obs.producer_id == "opaque-author"
        assert isinstance(obs, Observation)
        assert obs.to_dict()["producer_id"] == "opaque-author"

    def test_survives_reload(self, tmp_path: Path):
        """Data persists across directory instances."""
        d1 = ActorDirectory(tmp_path)
        d1.put(_entry(opaque_id="id-1", team="alpha"))

        d2 = ActorDirectory(tmp_path)
        result = d2.get("id-1")
        assert result is not None
        assert result.team == "alpha"
