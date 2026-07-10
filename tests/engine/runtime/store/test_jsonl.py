"""Tests for JSONL reader/writer with advisory locking."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from verdx.engine.runtime.store.jsonl import MalformedLineError, append_jsonl, read_jsonl

if TYPE_CHECKING:
    from pathlib import Path


class TestReadJsonl:
    def test_nonexistent_file_returns_empty(self, tmp_path: Path):
        assert read_jsonl(tmp_path / "missing.jsonl") == []

    def test_empty_file_returns_empty(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        assert read_jsonl(path) == []

    def test_single_record(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        path.write_text('{"key": "value"}\n')
        records = read_jsonl(path)
        assert len(records) == 1
        assert records[0] == {"key": "value"}

    def test_multiple_records(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        path.write_text('{"a": 1}\n{"b": 2}\n{"c": 3}\n')
        records = read_jsonl(path)
        assert len(records) == 3
        assert records[0]["a"] == 1
        assert records[2]["c"] == 3

    def test_tolerates_trailing_malformed_line(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        path.write_text('{"good": true}\n{broken\n')
        records = read_jsonl(path)
        assert len(records) == 1
        assert records[0] == {"good": True}

    def test_rejects_non_trailing_malformed_line(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        path.write_text('{broken\n{"good": true}\n')
        with pytest.raises(MalformedLineError) as exc_info:
            read_jsonl(path)
        assert exc_info.value.line_number == 1

    def test_truncates_long_content_in_error_message(self, tmp_path: Path):
        """Long malformed content is truncated in message but preserved in attribute."""
        path = tmp_path / "data.jsonl"
        long_content = "{" + "x" * 150 + "\n"
        path.write_text(long_content + '{"good": true}\n')
        with pytest.raises(MalformedLineError) as exc_info:
            read_jsonl(path)

        # Full content preserved in attribute
        assert len(exc_info.value.content) > 100
        assert exc_info.value.content == ("{" + "x" * 150).strip()

        # Message contains truncated content
        error_msg = str(exc_info.value)
        assert "..." in error_msg
        # Check that the message doesn't contain the full 150 chars
        assert len(error_msg) < len(exc_info.value.content) + 50

    def test_ignores_blank_lines(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        path.write_text('{"a": 1}\n\n{"b": 2}\n')
        records = read_jsonl(path)
        assert len(records) == 2

    def test_no_trailing_newline_ok(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        path.write_text('{"a": 1}')
        records = read_jsonl(path)
        assert len(records) == 1


class TestAppendJsonl:
    def test_creates_file(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        append_jsonl(path, [{"key": "value"}])
        assert path.exists()
        records = read_jsonl(path)
        assert len(records) == 1

    def test_appends_to_existing(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        append_jsonl(path, [{"a": 1}])
        append_jsonl(path, [{"b": 2}])
        records = read_jsonl(path)
        assert len(records) == 2

    def test_multiple_records_at_once(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        append_jsonl(path, [{"a": 1}, {"b": 2}, {"c": 3}])
        records = read_jsonl(path)
        assert len(records) == 3

    def test_empty_records_no_op(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        append_jsonl(path, [])
        assert not path.exists()

    def test_creates_parent_directories(self, tmp_path: Path):
        path = tmp_path / "deep" / "nested" / "data.jsonl"
        append_jsonl(path, [{"ok": True}])
        assert path.exists()

    def test_deterministic_serialization(self, tmp_path: Path):
        path = tmp_path / "data.jsonl"
        append_jsonl(path, [{"b": 2, "a": 1}])
        line = path.read_text().strip()
        parsed = json.loads(line)
        assert list(parsed.keys()) == ["a", "b"]

    def test_concurrent_appends_preserve_records(self, tmp_path: Path):
        """Multiple sequential appends all persist."""
        path = tmp_path / "data.jsonl"
        for i in range(10):
            append_jsonl(path, [{"i": i}])
        records = read_jsonl(path)
        assert len(records) == 10
        assert [r["i"] for r in records] == list(range(10))
