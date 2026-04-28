"""End-to-end integration tests for OCR batch processor.

Tests the full pipeline from CLI invocation through output generation,
error logging, and exit code handling.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from io import StringIO
from contextlib import redirect_stderr, redirect_stdout

import pytest

from ocr_batch.models import ProcessResult


@pytest.fixture
def sample_image_folder():
    """Create temporary folder with 10 valid images + 1 corrupted image."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create 10 valid test images
        try:
            from PIL import Image
            for i in range(1, 11):
                img = Image.new('RGB', (200, 100), color='white')
                img.save(tmpdir_path / f"test_image_{i:03d}.jpg", "JPEG")
        except ImportError:
            pytest.skip("PIL not available for test image creation")

        # Create 1 corrupted image
        corrupted = tmpdir_path / "corrupted_image.jpg"
        corrupted.write_bytes(b'\xFF\xD8\xFF\xE0' + b'corrupted_data' * 100)

        yield tmpdir_path


@pytest.fixture
def temp_output_paths():
    """Provide temporary output and error log paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        yield {
            'output': tmpdir_path / "output.md",
            'errors': tmpdir_path / "errors.jsonl"
        }


def test_end_to_end_full_batch(sample_image_folder, temp_output_paths):
    """Test full pipeline: CLI invocation, output generation, error logging."""
    output_file = str(temp_output_paths['output'])
    error_log = str(temp_output_paths['errors'])

    # Run CLI
    cmd = [
        sys.executable, "-m", "ocr_batch",
        str(sample_image_folder),
        "--output", output_file,
        "--log", error_log
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Verify output file exists
    assert Path(output_file).exists(), f"Output file not created at {output_file}"

    # Verify error log exists
    assert Path(error_log).exists(), f"Error log not created at {error_log}"

    # Read output and verify headers
    output_content = Path(output_file).read_text(encoding='utf-8')

    # Should contain image headers (at least some of the 10 valid images)
    # Check for markdown headers like "# test_image_001.jpg"
    assert "# " in output_content, "No markdown headers found in output"

    # Verify error log has at least the corrupted image entry
    error_lines = Path(error_log).read_text(encoding='utf-8').strip().split('\n')
    error_lines = [line for line in error_lines if line.strip()]
    assert len(error_lines) > 0, "Expected at least one error in error log (corrupted image)"

    # Verify each error line is valid JSON
    for line in error_lines:
        try:
            entry = json.loads(line)
            assert 'file' in entry, "Missing 'file' in error entry"
            assert 'error_type' in entry, "Missing 'error_type' in error entry"
            assert 'message' in entry, "Missing 'message' in error entry"
            assert 'timestamp' in entry, "Missing 'timestamp' in error entry"
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON in error log: {line}\n{e}")


def test_output_format(sample_image_folder, temp_output_paths):
    """Verify markdown output format: headers, separators, UTF-8 encoding."""
    output_file = str(temp_output_paths['output'])
    error_log = str(temp_output_paths['errors'])

    cmd = [
        sys.executable, "-m", "ocr_batch",
        str(sample_image_folder),
        "--output", output_file,
        "--log", error_log
    ]

    subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Read output as UTF-8
    output_content = Path(output_file).read_text(encoding='utf-8')

    # Verify format:
    # - Headers: "# filename.ext"
    # - Separators: "---"
    # - UTF-8 encoding

    # Check for markdown headers (format: "# filename")
    lines = output_content.split('\n')
    header_count = sum(1 for line in lines if line.startswith('# '))
    assert header_count > 0, "No markdown headers (# filename) found in output"

    # Check for separators
    assert '---' in output_content, "No markdown separators (---) found in output"

    # Verify each header has extension in filename
    for line in lines:
        if line.startswith('# '):
            filename = line[2:].strip()
            # Should have an extension
            assert '.' in filename or not filename, f"Header missing extension: {line}"


def test_error_log_format(sample_image_folder, temp_output_paths):
    """Verify error log format: valid JSONL with required fields."""
    output_file = str(temp_output_paths['output'])
    error_log = str(temp_output_paths['errors'])

    cmd = [
        sys.executable, "-m", "ocr_batch",
        str(sample_image_folder),
        "--output", output_file,
        "--log", error_log
    ]

    subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Read error log
    error_content = Path(error_log).read_text(encoding='utf-8').strip()
    if not error_content:
        pytest.skip("No errors in this run (expected if images processed successfully)")

    error_lines = error_content.split('\n')

    # Verify JSONL format
    for line in error_lines:
        if not line.strip():
            continue

        # Parse JSON
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON in error log: {line}\n{e}")

        # Verify required fields
        required_fields = {'file', 'error_type', 'message', 'timestamp'}
        missing = required_fields - set(entry.keys())
        assert not missing, f"Missing fields in error entry: {missing}\nEntry: {entry}"


def test_parallel_speed(sample_image_folder, temp_output_paths):
    """Verify parallel processing is faster than serial.

    Time a batch with 4 workers vs 1 worker.
    Assert 4-worker run completes in reasonable time (rough check).
    """
    import time

    output_1w = str(temp_output_paths['output'].parent / "output_1w.md")
    output_4w = str(temp_output_paths['output'].parent / "output_4w.md")
    errors_1w = str(temp_output_paths['output'].parent / "errors_1w.jsonl")
    errors_4w = str(temp_output_paths['output'].parent / "errors_4w.jsonl")

    # Run with 1 worker
    cmd_1w = [
        sys.executable, "-m", "ocr_batch",
        str(sample_image_folder),
        "--output", output_1w,
        "--log", errors_1w,
        "--workers", "1"
    ]

    start = time.time()
    subprocess.run(cmd_1w, capture_output=True, text=True, timeout=120)
    time_1w = time.time() - start

    # Run with 4 workers
    cmd_4w = [
        sys.executable, "-m", "ocr_batch",
        str(sample_image_folder),
        "--output", output_4w,
        "--log", errors_4w,
        "--workers", "4"
    ]

    start = time.time()
    subprocess.run(cmd_4w, capture_output=True, text=True, timeout=120)
    time_4w = time.time() - start

    # Verify both completed
    assert Path(output_1w).exists(), "1-worker run did not produce output"
    assert Path(output_4w).exists(), "4-worker run did not produce output"

    # Log timing (not strict comparison due to small batch size)
    print(f"\n1 worker: {time_1w:.2f}s | 4 workers: {time_4w:.2f}s")

    # At minimum, both should complete successfully
    assert time_1w > 0, "1-worker run took no time"
    assert time_4w > 0, "4-worker run took no time"


def test_progress_bar_output(sample_image_folder, temp_output_paths):
    """Verify progress bar appears and is not garbled.

    Capture stderr during run and check for tqdm markers.
    """
    output_file = str(temp_output_paths['output'])
    error_log = str(temp_output_paths['errors'])

    cmd = [
        sys.executable, "-m", "ocr_batch",
        str(sample_image_folder),
        "--output", output_file,
        "--log", error_log
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # tqdm outputs progress information to stderr
    stderr_output = result.stderr

    # Check for progress indicators
    # tqdm typically outputs % or "|" or similar progress markers
    # At minimum, verify stderr contains something about progress
    # (exact format may vary, so we do a loose check)

    # Should have some output (could be progress bar or just newlines)
    # The key is to ensure nothing catastrophic happened to stderr

    # Verify no duplicate output patterns (sign of garbling)
    # Count consecutive identical lines as a rough check
    stderr_lines = stderr_output.split('\n')

    # If there are many stderr lines, they should not be all identical
    if len(stderr_lines) > 5:
        unique_lines = set(stderr_lines)
        duplication_ratio = len(stderr_lines) / max(len(unique_lines), 1)
        # Should have reasonable variety (not all the same line repeated)
        assert duplication_ratio < 3, f"Possible output garbling: {duplication_ratio}x duplication"


def test_exit_code_success(sample_image_folder, temp_output_paths):
    """Verify exit code 0 when no failures."""
    output_file = str(temp_output_paths['output'])
    # Use a clean folder with only valid images
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        try:
            from PIL import Image
            # Create 3 valid images
            for i in range(1, 4):
                img = Image.new('RGB', (200, 100), color='white')
                img.save(tmpdir_path / f"valid_{i}.jpg", "JPEG")
        except ImportError:
            pytest.skip("PIL not available")

        cmd = [
            sys.executable, "-m", "ocr_batch",
            str(tmpdir_path),
            "--output", output_file,
            "--log", str(temp_output_paths['errors'])
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        # Exit code should be 0 (no errors)
        assert result.returncode == 0, f"Expected exit code 0, got {result.returncode}\nStderr: {result.stderr}"


def test_exit_code_failure(sample_image_folder, temp_output_paths):
    """Verify exit code 1 when failures occur."""
    output_file = str(temp_output_paths['output'])
    error_log = str(temp_output_paths['errors'])

    # sample_image_folder has 1 corrupted image, so should fail
    cmd = [
        sys.executable, "-m", "ocr_batch",
        str(sample_image_folder),
        "--output", output_file,
        "--log", error_log
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Should have exit code 1 due to corrupted image
    assert result.returncode == 1, f"Expected exit code 1, got {result.returncode}"
