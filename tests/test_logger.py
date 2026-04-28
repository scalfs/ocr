"""Unit tests for logger.py"""

import json
import tempfile
from pathlib import Path

import pytest

from ocr_batch.logger import ErrorLogger, format_timestamp_utc


class TestFormatTimestampUtc:
    """Tests for format_timestamp_utc function."""

    def test_format_timestamp_utc_format(self):
        """Test that timestamp is in ISO 8601 UTC format."""
        ts = format_timestamp_utc()
        # Should match pattern: YYYY-MM-DDTHH:MM:SSZ
        assert len(ts) == 20
        assert ts[-1] == "Z"
        assert "T" in ts
        parts = ts.split("T")
        assert len(parts) == 2
        assert len(parts[0]) == 10  # YYYY-MM-DD

    def test_format_timestamp_utc_parseable(self):
        """Test that timestamp can be parsed back."""
        from datetime import datetime

        ts = format_timestamp_utc()
        # Should be parseable as ISO 8601
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed is not None


class TestErrorLogger:
    """Tests for ErrorLogger class."""

    def test_errorlogger_init(self):
        """Test ErrorLogger initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "errors.jsonl"
            logger = ErrorLogger(output_path)
            assert logger.output_path == output_path

    def test_log_error_creates_file(self):
        """Test that log_error creates output file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "errors.jsonl"
            logger = ErrorLogger(output_path)
            logger.log_error("image.jpg", "ValueError", "Test error message")
            assert output_path.exists()

    def test_log_error_writes_json(self):
        """Test that log_error writes valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "errors.jsonl"
            logger = ErrorLogger(output_path)
            logger.log_error("image.jpg", "ValueError", "Test error")
            with open(output_path, "r") as f:
                line = f.readline()
                data = json.loads(line)
                assert data["file"] == "image.jpg"
                assert data["error_type"] == "ValueError"
                assert data["message"] == "Test error"

    def test_log_error_includes_timestamp(self):
        """Test that log_error includes timestamp when timestamp_utc=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "errors.jsonl"
            logger = ErrorLogger(output_path, timestamp_utc=True)
            logger.log_error("image.jpg", "ValueError", "Test error")
            with open(output_path, "r") as f:
                data = json.loads(f.readline())
                assert "timestamp" in data
                assert data["timestamp"].endswith("Z")

    def test_log_error_custom_timestamp(self):
        """Test log_error with custom timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "errors.jsonl"
            logger = ErrorLogger(output_path)
            custom_ts = "2026-04-28T12:00:00Z"
            logger.log_error("image.jpg", "ValueError", "Test", timestamp=custom_ts)
            with open(output_path, "r") as f:
                data = json.loads(f.readline())
                assert data["timestamp"] == custom_ts

    def test_log_error_includes_duration(self):
        """Test log_error with duration_s."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "errors.jsonl"
            logger = ErrorLogger(output_path)
            logger.log_error("image.jpg", "ValueError", "Test error", duration_s=1.234)
            with open(output_path, "r") as f:
                data = json.loads(f.readline())
                assert data["duration_s"] == 1.234

    def test_log_error_truncates_long_message(self):
        """Test that messages longer than 500 chars are truncated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "errors.jsonl"
            logger = ErrorLogger(output_path)
            long_msg = "x" * 1000
            logger.log_error("image.jpg", "ValueError", long_msg)
            with open(output_path, "r") as f:
                data = json.loads(f.readline())
                assert len(data["message"]) == 500

    def test_log_error_multiple_lines(self):
        """Test that multiple errors are written as separate lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "errors.jsonl"
            logger = ErrorLogger(output_path)
            logger.log_error("image1.jpg", "ValueError", "Error 1")
            logger.log_error("image2.jpg", "OSError", "Error 2")
            with open(output_path, "r") as f:
                lines = f.readlines()
                assert len(lines) == 2
                data1 = json.loads(lines[0])
                data2 = json.loads(lines[1])
                assert data1["file"] == "image1.jpg"
                assert data2["file"] == "image2.jpg"

    def test_log_error_flush(self):
        """Test that errors are flushed immediately (crash-safe)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "errors.jsonl"
            logger = ErrorLogger(output_path)
            logger.log_error("image.jpg", "ValueError", "Test")
            # Should be readable immediately without closing the logger
            with open(output_path, "r") as f:
                data = json.loads(f.readline())
                assert data is not None
