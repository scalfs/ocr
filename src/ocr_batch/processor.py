"""Batch processor with multiprocessing pool orchestration.

Implements pool lifecycle, warmup step (C2/C3 mitigation), and result streaming.
Uses imap_unordered for memory efficiency (S1) and main-process tqdm for clean
progress display (S2).
"""

import logging
import multiprocessing as mp
import os
from pathlib import Path
from typing import Iterator

from tqdm import tqdm

from . import worker
from .models import ProcessResult

logger = logging.getLogger(__name__)

# VRAM budget per RapidOCR torch worker: ~420 MB.
# RTX 3050 6GB → safe headroom for 3 concurrent GPU workers.
_GPU_WORKER_CAP = 3
_CPU_WORKER_CAP = 4


def detect_gpu() -> bool:
    """Return True if a CUDA-capable GPU is available."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def warmup_models(use_gpu: bool = False) -> None:
    """Warm up RapidOCR model caches before spawning the pool.

    C2/C3 MITIGATION: Initialises DocumentConverter once in the main process so
    model files are downloaded and cached before N workers start simultaneously.
    Without this, concurrent workers race to download the same ONNX/torch weights.

    After warmup the converter is deleted and, for GPU runs, VRAM is explicitly
    freed so workers inherit a clean VRAM budget.

    Args:
        use_gpu: If True, warm up with torch backend (verifies GPU path).
    """
    print("Warming up models...", flush=True)

    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import RapidOcrOptions, PdfPipelineOptions
        from docling.document_converter import DocumentConverter, ImageFormatOption

        backend = "torch" if use_gpu else "onnxruntime"
        ocr_options = RapidOcrOptions(
            lang=["english"],
            backend=backend,
            print_verbose=False,
        )
        pipeline_options = PdfPipelineOptions(do_ocr=True, ocr_options=ocr_options)
        converter = DocumentConverter(
            format_options={
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options)
            }
        )
        logger.debug("DocumentConverter initialised for warmup (backend=%s)", backend)
        del converter

        # Free VRAM retained by the warmup converter so workers start with a
        # clean budget. Without this, ~420 MB stays pinned in the main process.
        if use_gpu:
            try:
                import torch
                import gc
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

    except Exception as e:
        logger.warning("Model warmup had an issue (may be OK if models cached): %s", e)

    print("Models ready.", flush=True)


def process_all(
    paths: list[Path],
    worker_count: int | None = None,
    use_gpu: bool | None = None,
    verbose: bool = False,
) -> Iterator[ProcessResult]:
    """Process all images through multiprocessing pool.

    IMPORTANT: Must be called after multiprocessing.set_start_method("spawn") is
    set in __main__.py (C4 mitigation). Without spawn, PyTorch/ONNX on macOS
    crashes with segfaults.

    GPU auto-detection: if use_gpu is None, CUDA presence is checked at runtime.
    Worker cap differs by device:
      - GPU (torch backend):       max _GPU_WORKER_CAP (VRAM budget)
      - CPU (onnxruntime backend): max _CPU_WORKER_CAP (RAM budget)

    Args:
        paths: List of Path objects to process.
        worker_count: Worker processes. None → auto (capped by device type).
        use_gpu: True/False to force a device. None → auto-detect CUDA.
        verbose: If True, enable debug logging.

    Yields:
        ProcessResult objects as images complete (unordered).
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    # Auto-detect GPU if caller did not specify
    if use_gpu is None:
        use_gpu = detect_gpu()
        logger.debug("GPU auto-detected: %s", use_gpu)

    # Worker count: apply per-device cap
    cap = _GPU_WORKER_CAP if use_gpu else _CPU_WORKER_CAP
    if worker_count is None:
        cpu_count = os.cpu_count() or cap
        worker_count = min(cpu_count, cap)
    else:
        worker_count = min(worker_count, cap)

    logger.debug(
        "Starting pool: %d workers, %d images, gpu=%s",
        worker_count, len(paths), use_gpu,
    )

    # C2/C3 MITIGATION: pre-populate model caches before pool spawn
    warmup_models(use_gpu=use_gpu)

    with mp.Pool(
        processes=worker_count,
        initializer=worker._init_worker,
        initargs=(use_gpu,),
    ) as pool:
        result_iter = pool.imap_unordered(worker.process_image, paths, chunksize=1)
        for result in tqdm(result_iter, total=len(paths), desc="Processing images"):
            yield result
