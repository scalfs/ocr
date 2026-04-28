"""Data models for OCR batch processing.

Contains the ProcessResult dataclass that encapsulates the output of a single
image processing operation, including success/error state and timing information.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProcessResult:
    """Result of processing a single image through OCR pipeline.

    Attributes:
        path: Path to the processed image file
        markdown: Extracted text as markdown, or None if processing failed
        error: Error message/description, or None if processing succeeded
        error_type: Exception class name (e.g., "ValueError"), or None on success
        duration_s: Total time spent processing this image in seconds
    """

    path: Path
    markdown: str | None
    error: str | None
    error_type: str | None
    duration_s: float
