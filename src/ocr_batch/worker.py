"""Worker process functions for multiprocessing pool.

Implements the initializer pattern to load DocumentConverter once per worker process
(C1 mitigation: converter is not picklable, so each worker must construct its own).

The converter is stored in a module-level global _converter variable, reused across
all image processing calls in that worker's lifetime.

OCR backend: RapidOCR
- GPU path  (use_gpu=True):  backend='torch'     — CUDA inference, ~1.3s/image
- CPU path  (use_gpu=False): backend='onnxruntime' — ONNX CPU,     ~1.6s/image
"""

import gc
import time
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import RapidOcrOptions, PdfPipelineOptions
from docling.datamodel.document import ConversionStatus
from docling.document_converter import DocumentConverter, ImageFormatOption

from .models import ProcessResult

# C1 MITIGATION: Module-level global converter, instantiated once per worker process
_converter = None
_use_gpu = False


def _init_worker(use_gpu: bool = False) -> None:
    """Initialize worker process with DocumentConverter instance.

    Called exactly once per worker process via Pool(initializer=...).

    Args:
        use_gpu: If True, use RapidOCR torch backend (CUDA). Otherwise onnxruntime (CPU).
    """
    global _converter, _use_gpu
    _use_gpu = use_gpu

    try:
        backend = "torch" if use_gpu else "onnxruntime"
        ocr_options = RapidOcrOptions(
            lang=["english"],
            backend=backend,
            print_verbose=False,
        )
        pipeline_options = PdfPipelineOptions(
            do_ocr=True,
            ocr_options=ocr_options,
        )
        _converter = DocumentConverter(
            format_options={
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options)
            }
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"DocumentConverter initialization warning: {e}")


def process_image(path: Path) -> ProcessResult:
    """Process a single image through OCR pipeline.

    Uses the pre-loaded _converter global. All exceptions are caught and returned
    as error state — never raises.

    Args:
        path: Path to image file to process.

    Returns:
        ProcessResult with markdown on success or error fields on failure.
    """
    global _converter, _use_gpu

    start_time = time.perf_counter()

    if _converter is None:
        return ProcessResult(
            path=path,
            markdown=None,
            error="DocumentConverter not initialized in worker",
            error_type="RuntimeError",
            duration_s=time.perf_counter() - start_time,
        )

    try:
        result = _converter.convert(path, raises_on_error=False)

        if result.status in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            markdown = result.document.export_to_markdown()
            return ProcessResult(
                path=path,
                markdown=markdown,
                error=None,
                error_type=None,
                duration_s=time.perf_counter() - start_time,
            )
        else:
            if getattr(result, "errors", None):
                error_msg = str(result.errors[0])
            else:
                error_msg = f"Conversion failed with status={result.status}"
            return ProcessResult(
                path=path,
                markdown=None,
                error=error_msg,
                error_type="ConversionError",
                duration_s=time.perf_counter() - start_time,
            )

    except Exception as e:
        return ProcessResult(
            path=path,
            markdown=None,
            error=str(e),
            error_type=type(e).__name__,
            duration_s=time.perf_counter() - start_time,
        )

    finally:
        # Mitigate incremental memory growth between images.
        gc.collect()
        if _use_gpu:
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
