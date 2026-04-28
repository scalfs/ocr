"""Incremental output writer for OCR results.

Provides OutputWriter class that incrementally writes ProcessResult objects to a Markdown file
with UTF-8 encoding, crash-safe flushing, and proper filename headers.

Hazards addressed:
- S4: UTF-8 encoding — always open with encoding="utf-8"
- M8: Header collisions — use full filename (e.g., "# image.jpg" not "# image")
"""

from pathlib import Path

from ocr_batch.models import ProcessResult


class OutputWriter:
    """Writes OCR results incrementally to a Markdown file.

    Each result is appended as:
        # {filename}

        {markdown_content}

        ---

    Files are opened in append mode with explicit UTF-8 encoding and flushed after each write
    for crash-safety. Full filenames are used in headers to avoid collisions.
    """

    def __init__(self, output_path: Path, original_paths: list[Path] | None = None):
        """Initialize the output writer.

        Args:
            output_path: Path to the output Markdown file (will be created or appended to)
            original_paths: Optional list of original image paths for indexing/ordering.
                           Pre-computed index map can be used for optional future sorting.
        """
        self.output_path = Path(output_path)
        self.original_paths = original_paths or []

        # Pre-compute index map for optional future sorting by original order
        self._index_map = {path: idx for idx, path in enumerate(self.original_paths)}

        # Initialize counts
        self._processed = 0
        self._skipped = 0

    def write_result(self, result: ProcessResult) -> bool:
        """Write a ProcessResult to the output file.

        If result.markdown is None (processing failed), the result is skipped (not written).
        If result.markdown is not None, the result is appended to the output file with:
        - Full filename header: # {result.path.name}
        - Extracted markdown content
        - Section separator: ---

        File is opened in append mode with UTF-8 encoding and flushed after each write
        for crash-safety.

        Args:
            result: ProcessResult object with path, markdown, and error information

        Returns:
            True if result was written, False if skipped (error case)
        """
        if result.markdown is None:
            # Skip failed results (errors not written to output file)
            self._skipped += 1
            return False

        # Append result to output file
        with open(
            self.output_path, mode="a", encoding="utf-8"
        ) as f:
            # Write full filename header (M8 mitigation: full filename, not stem)
            f.write(f"# {result.path.name}\n\n")

            # Write extracted markdown content
            f.write(f"{result.markdown}\n\n")

            # Write section separator
            f.write("---\n\n")

            # Flush for crash-safety (S4 mitigation: ensure write hits disk)
            f.flush()

        self._processed += 1
        return True

    def get_counts(self) -> dict:
        """Get the current write counts.

        Returns:
            Dictionary with keys:
                - processed: Number of results written to output file
                - skipped: Number of results skipped (errors not written)
        """
        return {
            "processed": self._processed,
            "skipped": self._skipped,
        }
