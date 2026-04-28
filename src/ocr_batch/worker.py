"""Worker process functions for multiprocessing pool.

Implements the initializer pattern to load DocumentConverter once per worker process
(C1 mitigation: converter is not picklable, so each worker must construct its own).

The converter is stored in a module-level global _converter variable, reused across
all image processing calls in that worker's lifetime.
"""

import time
from pathlib import Path

from docling.datamodel.pipeline_options import EasyOcrOptions
from docling.document_converter import DocumentConverter

from .models import ProcessResult

# C1 MITIGATION: Module-level global converter, instantiated once per worker process
# This avoids the PicklingError that would occur if we tried to pass a converter
# instance to a worker via multiprocessing pool.
_converter = None


def _init_worker(use_gpu: bool = False) -> None:
    """Initialize worker process with DocumentConverter instance.

    Called exactly once per worker process when the Pool is created with
    initializer=_init_worker. Builds and caches the converter globally so
    subsequent process_image() calls reuse the loaded model.

    C1 MITIGATION: Instantiates DocumentConverter here (not picklable) so each
    worker gets its own non-serialized copy.

    Args:
        use_gpu: If True, use GPU for EasyOCR inference; default False for portability.

    Returns:
        None. Sets global _converter variable.
    """
    global _converter

    try:
        # Configure EasyOCR with English language only (reduces model downloads)
        ocr_options = EasyOcrOptions(lang=["en"], use_gpu=use_gpu)

        # Instantiate converter with OCR pipeline
        _converter = DocumentConverter(
            format_options={"image": {"pipeline_options": {"ocr_options": ocr_options}}}
        )
    except Exception as e:
        # Log but don't fail the worker startup — models may already be cached
        # or converter may still be usable for non-OCR content
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"DocumentConverter initialization warning: {e}")
        # Allow worker to continue; process_image will handle any missing converter


def process_image(path: Path) -> ProcessResult:
    """Process a single image through OCR pipeline.

    Called per image in the worker pool. Uses the pre-loaded _converter global.
    All exceptions are caught and returned as error state, not raised.

    Args:
        path: Path to image file to process.

    Returns:
        ProcessResult with markdown (success) or error fields (failure).
            Always returns — never raises an exception.
    """
    global _converter

    start_time = time.perf_counter()

    if _converter is None:
        duration = time.perf_counter() - start_time
        return ProcessResult(
            path=path,
            markdown=None,
            error="DocumentConverter not initialized in worker",
            error_type="RuntimeError",
            duration_s=duration,
        )

    try:
        # Convert image, catching errors at the Docling level
        result = _converter.convert(path, raises_on_error=False)

        # Check if conversion succeeded
        if result.status.success:
            markdown = result.document.export_to_markdown()
            duration = time.perf_counter() - start_time
            return ProcessResult(
                path=path,
                markdown=markdown,
                error=None,
                error_type=None,
                duration_s=duration,
            )
        else:
            # Docling conversion failed gracefully
            error_msg = str(result.status.error_message) if result.status.error_message else "Unknown error"
            duration = time.perf_counter() - start_time
            return ProcessResult(
                path=path,
                markdown=None,
                error=error_msg,
                error_type=type(result.status.error_message).__name__ if result.status.error_message else "ConversionError",
                duration_s=duration,
            )

    except Exception as e:
        # Catch any unexpected exceptions (timeouts, memory, etc.)
        duration = time.perf_counter() - start_time
        return ProcessResult(
            path=path,
            markdown=None,
            error=str(e),
            error_type=type(e).__name__,
            duration_s=duration,
        )
