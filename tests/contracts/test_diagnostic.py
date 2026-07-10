"""Tests for diagnostic event model and redaction."""

from __future__ import annotations

from verdx.contracts.diagnostic import DiagnosticEvent, redact

# ===========================================================================
# Redaction
# ===========================================================================


class TestRedact:
    def test_redacts_password(self):
        result = redact("password=secret123")
        assert result == "[REDACTED]"

    def test_redacts_password_case_insensitive(self):
        result = redact("PASSWORD=secret123")
        assert result == "[REDACTED]"

    def test_redacts_token(self):
        result = redact("token=abc123def")
        assert result == "[REDACTED]"

    def test_redacts_api_key_underscore(self):
        result = redact("api_key=xyz789")
        assert result == "[REDACTED]"

    def test_redacts_api_key_hyphen(self):
        result = redact("api-key=xyz789")
        assert result == "[REDACTED]"

    def test_redacts_secret(self):
        result = redact("secret: my-secret-value")
        assert result == "[REDACTED]"

    def test_redacts_aws_access_key(self):
        result = redact("AKIAIOSFODNN7EXAMPLE")  # gitleaks:allow (AWS's published example key)
        assert result == "[REDACTED]"

    def test_redacts_home_directory_path(self):
        result = redact("/home/username/project/file.txt")
        assert result == "[REDACTED]"

    def test_preserves_clean_string(self):
        result = redact("validator completed successfully")
        assert result == "validator completed successfully"

    def test_preserves_clean_path(self):
        result = redact("/usr/share/doc/readme.txt")
        assert result == "/usr/share/doc/readme.txt"

    def test_redacts_email_address(self):
        result = redact("actor: user@example.com performed action")
        assert "user@example.com" not in result
        assert "[REDACTED]" in result

    def test_redacts_env_var_assignment(self):
        result = redact("DB_HOST=prod-db.internal:5432")
        assert "prod-db.internal" not in result
        assert "[REDACTED]" in result

    def test_redacts_export_env_var(self):
        result = redact("export SECRET_KEY=abc123")
        assert "abc123" not in result
        assert "[REDACTED]" in result

    def test_redacts_tmp_path(self):
        result = redact("wrote to /tmp/verdx/run123.jsonl")
        assert "/tmp/" not in result
        assert "[REDACTED]" in result

    def test_redacts_var_path(self):
        result = redact("log at /var/log/app/error.log")
        assert "/var/log" not in result
        assert "[REDACTED]" in result

    def test_redacts_etc_path(self):
        result = redact("config from /etc/verdx/config.yml")
        assert "/etc/" not in result

    def test_redacts_multiple_patterns(self):
        result = redact("password=secret /home/user/file.txt token=abc")
        assert "[REDACTED]" in result
        assert "secret" not in result
        assert "/home/user/file.txt" not in result
        assert "abc" not in result

    def test_empty_string(self):
        result = redact("")
        assert result == ""


# ===========================================================================
# DiagnosticEvent
# ===========================================================================


class TestDiagnosticEvent:
    def test_to_dict(self):
        event = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="validator-start",
            validator_id="yaml-syntax",
            status="started",
            duration_ms=None,
            message="Starting validator",
            context={"target": "test.yaml"},
        )
        data = event.to_dict()
        assert data["timestamp"] == "2026-05-02T10:00:00Z"
        assert data["run_id"] == "run-123"
        assert data["phase"] == "validator-start"
        assert data["validator_id"] == "yaml-syntax"
        assert data["status"] == "started"
        assert data["duration_ms"] is None
        assert data["message"] == "Starting validator"
        assert data["context"] == {"target": "test.yaml"}

    def test_to_dict_redacts_message(self):
        event = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="validator-failed",
            validator_id="yaml-syntax",
            status="failed",
            duration_ms=100,
            message="Error: password=secret123",
            context={},
        )
        data = event.to_dict()
        assert data["message"] == "Error: [REDACTED]"

    def test_to_dict_redacts_context_strings(self):
        event = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="validator-failed",
            validator_id="yaml-syntax",
            status="failed",
            duration_ms=100,
            message="Error occurred",
            context={"error": "token=abc123def", "count": 5, "enabled": True},
        )
        data = event.to_dict()
        assert data["context"]["error"] == "[REDACTED]"
        assert data["context"]["count"] == 5
        assert data["context"]["enabled"] is True

    def test_from_dict_roundtrip(self):
        original = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="validator-complete",
            validator_id="yaml-syntax",
            status="completed",
            duration_ms=250,
            message="Validator completed",
            context={"checks_run": 10, "checks_passed": 8},
        )
        data = original.to_dict()
        restored = DiagnosticEvent.from_dict(data)

        # Note: message and context strings will be redacted in to_dict,
        # so we check the structure matches
        assert restored.timestamp == original.timestamp
        assert restored.run_id == original.run_id
        assert restored.phase == original.phase
        assert restored.validator_id == original.validator_id
        assert restored.status == original.status
        assert restored.duration_ms == original.duration_ms
        # Message and context may differ due to redaction

    def test_from_dict_with_none_validator_id(self):
        data = {
            "timestamp": "2026-05-02T10:00:00Z",
            "run_id": "run-123",
            "phase": "run-start",
            "validator_id": None,
            "status": "started",
            "duration_ms": None,
            "message": "Run started",
            "context": {},
        }
        event = DiagnosticEvent.from_dict(data)
        assert event.validator_id is None
        assert event.duration_ms is None

    def test_from_dict_minimal(self):
        data = {
            "timestamp": "2026-05-02T10:00:00Z",
            "run_id": "run-123",
            "phase": "scoring",
            "status": "started",
            "message": "Computing score",
        }
        event = DiagnosticEvent.from_dict(data)
        assert event.timestamp == "2026-05-02T10:00:00Z"
        assert event.run_id == "run-123"
        assert event.phase == "scoring"
        assert event.validator_id is None
        assert event.status == "started"
        assert event.duration_ms is None
        assert event.message == "Computing score"
        assert event.context == {}

    def test_to_dict_preserves_clean_message(self):
        event = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="validator-complete",
            validator_id="yaml-syntax",
            status="completed",
            duration_ms=150,
            message="Validator completed successfully",
            context={},
        )
        data = event.to_dict()
        assert data["message"] == "Validator completed successfully"

    def test_to_dict_redacts_home_directory_in_context(self):
        event = DiagnosticEvent(
            timestamp="2026-05-02T10:00:00Z",
            run_id="run-123",
            phase="validator-failed",
            validator_id="yaml-syntax",
            status="failed",
            duration_ms=50,
            message="File not found",
            context={"path": "/home/user/project/test.yaml"},
        )
        data = event.to_dict()
        assert data["context"]["path"] == "[REDACTED]"
