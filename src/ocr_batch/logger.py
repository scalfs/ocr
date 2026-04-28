"""Error logging to JSONL format.

Provides crash-safe, incremental error logging with JSON per line.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def format_timestamp_utc() -> str:
    """Return current time in ISO 8601 UTC format.

    Returns:
        String in format: 2026-04-28T12:00:00Z
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


class ErrorLogger:
    """Append-only JSONL error logger with crash-safe writes.

    Each error is written as a JSON object on its own line, with an immediate
    flush to ensure crash-safety (i.e., if the process dies, logged errors are
    not lost).
    """

    def __init__(self, output_path: Path, timestamp_utc: bool = True):
        """Initialize the error logger.

        Args:
            output_path: Path to the JSONL output file.
            timestamp_utc: If True, auto-generate timestamp for errors that don't provide one.
        """
        self.output_path = Path(output_path)
        self.timestamp_utc = timestamp_utc

    def log_error(
        self,
        filename: str,
        error_type: str,
        message: str,
        timestamp: str = None,
        duration_s: float = None,
    ) -> None:
        """Log an error to the JSONL file.

        Args:
            filename: Name of the file that caused the error (e.g., "image.jpg")
            error_type: Type of error (e.g., "ValueError", "CorruptedImage")
            message: Error message text (will be truncated to 500 chars)
            timestamp: ISO 8601 timestamp string. If None and timestamp_utc=True, auto-generated.
            duration_s: Optional processing duration in seconds.

        Side Effect:
            Writes one JSON line to the JSONL file and flushes immediately.
        """
        if timestamp is None and self.timestamp_utc:
            timestamp = format_timestamp_utc()

        # Truncate message to 500 chars to prevent bloat
        if message and len(message) > 500:
            message = message[:500]

        error_dict = {
            "file": filename,
            "error_type": error_type,
            "message": message,
        }

        if timestamp:
            error_dict["timestamp"] = timestamp

        if duration_s is not None:
            error_dict["duration_s"] = duration_s

        try:
            with open(self.output_path, "a", encoding="utf-8") as f:
                json.dump(error_dict, f)
                f.write("\n")
                f.flush()  # Crash-safe: flush immediately after each write
        except Exception as e:
            logger.error(f"Failed to log error for {filename}: {e}")
