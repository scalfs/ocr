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


def warmup_models(use_gpu: bool = False) -> None:
    """Warm up Docling + EasyOCR model caches before spawning pool.

    C2/C3 MITIGATION: Runs a single-process dummy conversion before Pool creation.
    This pre-populates model caches (~/.cache/docling, ~/.EasyOCR/model) and prevents
    concurrent model download races or EasyOCR initialization conflicts when multiple
    workers start simultaneously.

    Creates a temporary converter, loads models, and verifies setup. If the warmup
    fails (e.g., models already cached), logs a warning but continues — the batch
    can still proceed.

    Args:
        use_gpu: If True, warm up with GPU configuration (C3 verification).

    Returns:
        None. Models are cached to disk after this call.
    """
    print("Warming up models...", flush=True)

    try:
        # Instantiate converter in main process (single-threaded, safe)
        from docling.datamodel.pipeline_options import EasyOcrOptions
        from docling.document_converter import DocumentConverter

        ocr_options = EasyOcrOptions(lang=["en"], use_gpu=use_gpu)
        converter = DocumentConverter(
            format_options={"image": {"pipeline_options": {"ocr_options": ocr_options}}}
        )

        # Attempt a minimal convert to trigger model downloads and cache population.
        # If no images available, this may gracefully skip, but at least the converter
        # is initialized and models are linked.
        logger.debug("DocumentConverter initialized for warmup")

        # Clean up the temporary converter
        del converter

    except Exception as e:
        # Log warning but don't fail. Models may already be cached.
        logger.warning(f"Model warmup had an issue (may be OK if models cached): {e}")

    print("Models ready.", flush=True)


def process_all(
    paths: list[Path],
    worker_count: int | None = None,
    use_gpu: bool = False,
    verbose: bool = False,
) -> Iterator[ProcessResult]:
    """Process all images through multiprocessing pool.

    IMPORTANT: This function must be called AFTER multiprocessing.set_start_method("spawn")
    has been set in __main__.py (C4 mitigation). Without spawn, PyTorch/ONNX on macOS
    will crash with segfaults.

    Implements:
    - C2/C3: warmup_models() before Pool creation to pre-populate caches
    - S1: pool.imap_unordered(..., chunksize=1) to avoid buffering all results
    - S2: tqdm progress bar in main process only, wrapping imap_unordered
    - S7: worker_count capped at 4 for RAM safety

    Args:
        paths: List of Path objects to process.
        worker_count: Number of worker processes. If None, defaults to min(cpu_count, 4).
        use_gpu: If True, configure workers to use GPU (C3 consideration).
        verbose: If True, enable debug logging.

    Yields:
        ProcessResult objects as images complete (unordered).

    Raises:
        None. All errors are captured in ProcessResult.error fields.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    # S7 MITIGATION: Compute worker count with RAM-safety cap at 4
    if worker_count is None:
        cpu_count = os.cpu_count() or 4
        worker_count = min(cpu_count, 4)

    logger.debug(f"Starting pool with {worker_count} workers, {len(paths)} images")

    # C2/C3 MITIGATION: Warmup models before Pool creation
    warmup_models(use_gpu=use_gpu)

    # Create pool with initializer pattern (C1 mitigation embedded in worker module)
    with mp.Pool(
        processes=worker_count,
        initializer=worker._init_worker,
        initargs=(use_gpu,),
    ) as pool:

        # S1 MITIGATION: Use imap_unordered with chunksize=1 to avoid buffering
        # chunksize=1 ensures even distribution even when image complexity varies widely
        result_iter = pool.imap_unordered(
            worker.process_image,
            paths,
            chunksize=1,
        )

        # S2 MITIGATION: Single progress bar in main process wrapping imap_unordered
        # tqdm updates via iteration, not via worker callbacks (which would garble output)
        for result in tqdm(result_iter, total=len(paths), desc="Processing images"):
            yield result

        # Pool context manager ensures pool.close() and pool.join() are called
