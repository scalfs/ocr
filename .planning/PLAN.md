# Phase 1 Plan: Multiprocessing Foundation + Core Pipeline

## Phase Overview

**Goal:** Batch process ~300 images reliably with proper multiprocessing discipline, producing a single combined Markdown file with all extracted text and complete error logging.

**Success Criteria** (all 8 must be satisfied at phase completion):
1. User can invoke CLI with input folder path and receive combined Markdown output with all extracted text organized by filename headers
2. Processing completes across 300 images in parallel with 4 worker processes (default, tunable)
3. Failed images logged to errors.jsonl with filename, error type, and timestamp
4. Progress bar displays live during batch without visual corruption or duplicated output
5. Workers initialize safely on macOS/Windows using spawn with non-picklable DocumentConverter per-worker
6. Output written incrementally (crash-safe) with UTF-8 and full filenames in headers
7. Final summary shows processed/skipped/failed counts and elapsed time; exit code 0 or 1
8. All image files discovered, naturally sorted, validated; failures captured with full context

**Requirements:** REQ-01 through REQ-25 (all Phase 1 requirements)

**Build Order:**
```
Foundation (no dependencies)
  ├── discovery.py     — file discovery, sorting, validation
  └── logger.py        — error logging

Core Processing (depends on Foundation)
  ├── worker.py        — worker initializer, image processing
  └── processor.py     — Pool orchestration, progress tracking

Integration (depends on Core)
  ├── writer.py        — incremental output writing
  └── cli.py + __main__.py — entry point, argument parsing, orchestration
```

---

## Component Breakdown & Dependencies

### Foundation Layer (no external multiprocessing dependencies)

#### discovery.py
- **Responsibility:** File discovery, extension filtering, natural sorting
- **Input:** `Path` to folder
- **Output:** `list[Path]` of valid image files, naturally sorted
- **External Deps:** stdlib only + `natsort` (lightweight, pip install)
- **Key Functions:**
  - `discover_images(folder: Path, pattern: str = None) -> list[Path]`
  - Glob for all extensions: `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif`, `.webp`, `.bmp` (case-insensitive)
  - Return sorted via `natsort.natsorted()` to handle `img1, img2, img10` order
- **Hazards Addressed:**
  - M4: Natural sort (use natsort)
  - S5: Corrupted images (pre-validate with PIL.Image.open().verify())
  - S6: Large images (check dimensions, warn if >25 MP)
  - M6: WEBP codec (check PIL.features.check_codec("webp") at startup)
  - REQ-02, REQ-03, REQ-04, REQ-05 (discovery, filtering, sorting, validation)

#### logger.py
- **Responsibility:** Error logging to JSONL format
- **Input:** `ProcessResult` (with error fields)
- **Output:** `.jsonl` file with one JSON object per line
- **External Deps:** stdlib only (json, datetime)
- **Key Functions:**
  - `ErrorLogger.__init__(output_path: Path)`
  - `log_error(filename: str, error_type: str, message: str, timestamp: str, duration_s: float = None)`
  - Each line: `{"file": "img.jpg", "error_type": "ValueError", "message": "...", "timestamp": "2026-04-28T12:00:00Z", "duration_s": 0.123}`
  - Flush after each write (crash-safe)
- **Hazards Addressed:**
  - M5: Silent failures (return and log all errors)
  - REQ-16, REQ-17, REQ-18 (error logging format and content)

---

### Core Processing Layer (multiprocessing + OCR)

#### worker.py
- **Responsibility:** Per-worker image processing with model loading in initializer
- **Input:** `Path` to a single image
- **Output:** `ProcessResult` dataclass with markdown text or error
- **External Deps:** `docling`, `easyocr` (via docling), PIL/Pillow
- **Key Components:**
  ```python
  @dataclass
  class ProcessResult:
      path: Path
      markdown: str | None       # None on failure
      error: str | None          # None on success
      error_type: str | None     # exception class name
      duration_s: float
  ```
  - `_init_worker()` — called once per worker process
    - Instantiates `DocumentConverter` with `EasyOcrOptions(lang=["en"], use_gpu=False)`
    - Stores in module-level global `_converter`
  - `process_image(path: Path) -> ProcessResult` — called per image in worker
    - Calls `_converter.convert(path, raises_on_error=False)`
    - Extracts markdown via `result.document.export_to_markdown()`
    - Captures any exception and returns error state
    - Times the operation
- **Hazards Addressed:**
  - **C1:** DocumentConverter not picklable — each worker constructs its own via `_init_worker()`, stored in module global
  - **C2:** Model download race — mitigated by warmup step in processor (before Pool creation)
  - **C3:** EasyOCR concurrent init — mitigated by warmup step
  - REQ-06, REQ-09, REQ-10 (OCR processing, error handling)

#### processor.py
- **Responsibility:** Pool lifecycle, image distribution, progress tracking
- **Input:** `list[Path]` of images, worker count, output/error paths
- **Output:** Generator of `ProcessResult` as they complete
- **External Deps:** `multiprocessing`, `tqdm`, docling
- **Key Functions:**
  - `warmup_models(sample_path: Path = None)` — run single-process dummy conversion before Pool creation
    - Populates Docling + EasyOCR model caches
    - Prevents C2 (download race) and C3 (concurrent init)
    - If sample_path not provided, use a minimal synthetic image or first image in batch
  - `process_all(paths: list[Path], workers: int = 4, gpu: bool = False) -> Iterator[ProcessResult]`
    - Set start method: `multiprocessing.set_start_method("spawn", force=True)` (C4 mitigation)
    - Warmup before Pool creation
    - Create `Pool(processes=workers, initializer=_init_worker, initargs=())`
    - Feed to `pool.imap_unordered(process_image, paths, chunksize=1)` (S1 mitigation: no buffering)
    - Wrap with `tqdm(total=len(paths))` in main process (S2 mitigation: single progress bar)
    - Yield results as they arrive
    - Close pool and join on completion
- **Hazards Addressed:**
  - **C4:** Fork start method — set `spawn` explicitly inside `if __name__ == "__main__":`
  - **C2, C3:** Model race conditions — warmup step before Pool creation
  - **S1:** OOM from buffering — use `imap_unordered` not `pool.map()`
  - **S2:** tqdm garbling — single progress bar in main process wrapping imap_unordered
  - **S7:** Worker count — default to `min(os.cpu_count() or 4, 4)`, cap at 4 for RAM safety
  - REQ-07, REQ-08 (parallelism, progress bar)

---

### Integration Layer (output & CLI)

#### writer.py
- **Responsibility:** Incremental output file writing with ordering
- **Input:** Generator of `ProcessResult` in completion order, list of original paths for indexing
- **Output:** Single `.md` file with `# filename` headers, `---` separators, UTF-8 encoding
- **External Deps:** stdlib only (pathlib, io)
- **Key Functions:**
  - `OutputWriter.__init__(output_path: Path, original_paths: list[Path])`
    - Pre-compute index map: `{path: original_index}`
  - `write_result(result: ProcessResult) -> None`
    - If result.markdown is not None: append to output file
    - Format: `# {result.path.name}\n\n{result.markdown}\n\n---\n\n`
    - Open file in append mode, write, flush (crash-safe)
    - Track counts (processed, skipped)
  - `counts() -> dict` with keys: `processed`, `skipped`, `failed`
- **Hazards Addressed:**
  - **S3:** Non-deterministic order — (MVP: accept completion order; document clearly)
  - **S4:** UTF-8 encoding — always `open(..., encoding="utf-8")`
  - **M8:** Header collisions — use full filename (e.g., `# image.jpg` not `# image`)
  - REQ-11, REQ-12, REQ-13, REQ-14, REQ-15 (output format, headers, encoding, incremental writes)

#### cli.py
- **Responsibility:** Argument parsing, input validation, orchestration, summary printing
- **Input:** `sys.argv` (folder path, output file, workers, gpu flags)
- **Output:** Exit code (0 on success, 1 on any failures)
- **External Deps:** `typer`, `rich` (for console output)
- **Key Functions:**
  - Typer app with command:
    - `main(input_dir: str, output: str = "output.md", workers: int = None, gpu: bool = False, log: str = "errors.jsonl")`
  - Input validation: folder exists, is readable, output path is writable
  - Orchestrate: discovery → warmup → processor → writer + logger
  - Print final summary: `Processed: N | Skipped: M | Failed: K | Duration: HH:MM:SS`
  - Exit with code 0 if failed == 0, else 1
- **Hazards Addressed:**
  - **C4, C5:** Spawn start method + `__main__` guard — called from `__main__.py` inside `if __name__ == "__main__":`
  - **M3:** Docling DEBUG flood — set logging levels before any converter creation
  - **REQ-01, REQ-05, REQ-11, REQ-19, REQ-20, REQ-21, REQ-22, REQ-23, REQ-24, REQ-25** (CLI args, validation, summary, exit codes, help)

#### __main__.py
- **Responsibility:** Module entry point, multiprocessing guard, main invocation
- **Input:** None (invoked via `python -m ocr_batch` or `uv run ocr-batch`)
- **Output:** None (delegates to cli.main())
- **External Deps:** cli module
- **Key Code:**
  ```python
  if __name__ == "__main__":
      multiprocessing.set_start_method("spawn", force=True)  # C4 mitigation
      app()  # Typer app from cli.py
  ```
- **Hazards Addressed:**
  - **C4:** Fork safety — set spawn before any Pool construction
  - **C5:** Fork bomb prevention — __main__ guard prevents recursive worker spawning
  - **REQ-25** (module invocation `python -m ocr_batch`)

---

## Task Sequence

### Wave 1: Foundation (independent, can run in parallel)

#### Task 1.1: Create discovery.py module
- **Scope:** File discovery, filtering, natural sorting, pre-validation
- **Files Modified/Created:**
  - `src/ocr_batch/discovery.py` (new)
  - `src/ocr_batch/__init__.py` (create if not present)
- **Dependencies:** None (stdlib + natsort)
- **Time Estimate:** 15–20 min
- **Action:**
  - Implement `discover_images(folder: Path, pattern: str = None) -> list[Path]`
    - Glob for all supported extensions: `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif`, `.webp`, `.bmp`
    - Case-insensitive extension matching
    - Return sorted via `natsort.natsorted()`
  - Implement `validate_images(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]`
    - Use `PIL.Image.open(path).verify()` to pre-validate each file
    - Check dimensions: skip if > 25 MP (25000000 pixels)
    - Return valid paths list and list of (path, reason) tuples for skipped files
  - Implement `check_webp_support() -> bool`
    - Check `PIL.features.check_codec("webp")`
    - Log warning if not available
- **Verification:**
  - `pytest tests/test_discovery.py::test_discover_images` — finds all supported formats
  - `pytest tests/test_discovery.py::test_natural_sort` — img1, img2, img10 order (not lexicographic)
  - `pytest tests/test_discovery.py::test_validate_images` — skips corrupted files
- **Done When:**
  - Module imports cleanly
  - All functions pass unit tests
  - Pre-validates corrupted images without crashing

#### Task 1.2: Create logger.py module
- **Scope:** JSONL error logging
- **Files Modified/Created:**
  - `src/ocr_batch/logger.py` (new)
- **Dependencies:** stdlib only
- **Time Estimate:** 10–12 min
- **Action:**
  - Implement `ErrorLogger` class
    - `__init__(output_path: Path, timestamp_utc: bool = True)`
    - `log_error(filename: str, error_type: str, message: str, timestamp: str = None, duration_s: float = None)`
    - Open file in append mode, write JSON object, flush
    - Truncate message to 500 chars if longer
  - Implement `format_timestamp_utc() -> str` (ISO 8601 format)
- **Verification:**
  - `pytest tests/test_logger.py::test_jsonl_format` — writes valid JSONL
  - `pytest tests/test_logger.py::test_flush` — file flushed after each write
- **Done When:**
  - Module imports cleanly
  - Writes valid JSONL (one JSON per line, no corruption with concurrent writes)
  - All tests pass

---

### Wave 2: Core Processing (depends on Foundation types/imports)

#### Task 2.1: Create worker.py module with initializer and ProcessResult
- **Scope:** Worker process initialization, image processing, result modeling
- **Files Modified/Created:**
  - `src/ocr_batch/worker.py` (new)
  - `src/ocr_batch/models.py` (new, for ProcessResult dataclass)
- **Dependencies:** docling, easyocr, PIL, stdlib
- **Time Estimate:** 25–30 min
- **Critical Pitfall Mitigations:**
  - **C1 (DocumentConverter not picklable):** Implement `_init_worker()` that constructs DocumentConverter once per worker and stores in module-level global `_converter`. Worker processes will call this once at startup.
  - **C2 & C3 (Model download/init race):** Documented in processor.py; this module just uses the initialized converter.
- **Action:**
  - Create `ProcessResult` dataclass in models.py
    ```python
    @dataclass
    class ProcessResult:
        path: Path
        markdown: str | None
        error: str | None
        error_type: str | None
        duration_s: float
    ```
  - In worker.py:
    - Global variable `_converter = None`
    - Implement `_init_worker(use_gpu: bool = False) -> None`
      - Import and instantiate DocumentConverter with EasyOcrOptions
      - `ocr_options = EasyOcrOptions(lang=["en"], use_gpu=use_gpu)`
      - Build converter and assign to `_converter`
    - Implement `process_image(path: Path) -> ProcessResult`
      - Call `_converter.convert(path, raises_on_error=False)` (if signature supports it; else catch exceptions)
      - If successful: extract markdown via `result.document.export_to_markdown()`
      - If failed: capture error and error_type
      - Time the operation
      - Return ProcessResult
    - Import and use natsort for consistency
- **Verification:**
  - `pytest tests/test_worker.py::test_process_result_structure` — dataclass has all fields
  - `pytest tests/test_worker.py::test_init_worker` — _init_worker() builds converter without error (integration test)
  - `pytest tests/test_worker.py::test_process_image_success` — process a simple test image, returns valid markdown
  - `pytest tests/test_worker.py::test_process_image_error` — process corrupted image, returns error state (not exception)
- **Done When:**
  - ProcessResult type is well-formed and importable
  - _init_worker() constructs DocumentConverter correctly
  - process_image() handles both success and error paths
  - No exceptions leak to caller (all errors captured in ProcessResult)

#### Task 2.2: Create processor.py module with Pool orchestration
- **Scope:** Multiprocessing Pool setup, warmup, progress tracking
- **Files Modified/Created:**
  - `src/ocr_batch/processor.py` (new)
- **Dependencies:** multiprocessing, tqdm, docling, worker module
- **Time Estimate:** 30–35 min
- **Critical Pitfall Mitigations:**
  - **C4 (fork start method):** This module's `process_all()` must be called AFTER `multiprocessing.set_start_method("spawn")` is called in __main__.py. Document this clearly.
  - **C2 & C3 (model download/init race):** Implement `warmup_models()` that runs a single-process dummy conversion before Pool creation. This populates caches and prevents concurrent model downloads.
  - **S1 (OOM from buffering):** Use `pool.imap_unordered()` not `pool.map()`.
  - **S2 (tqdm garbling):** Single progress bar in main process, wrapping imap_unordered.
  - **S7 (worker count OOM):** Default to `min(os.cpu_count() or 4, 4)`.
- **Action:**
  - Implement `warmup_models(use_gpu: bool = False) -> None`
    - Create a temporary DocumentConverter instance (triggers model downloads)
    - Load EasyOcrOptions with specified GPU setting
    - Call convert() on a minimal synthetic image or first image in batch
    - Print to stdout: "Warming up models..." then "Models ready."
    - If warmup fails, log warning but continue (models may already be cached)
  - Implement `process_all(paths: list[Path], worker_count: int = None, use_gpu: bool = False, verbose: bool = False) -> Iterator[ProcessResult]`
    - Compute worker_count: if None, use `min(os.cpu_count() or 4, 4)`
    - Call `warmup_models(use_gpu=use_gpu)` before Pool creation
    - Create Pool with `processes=worker_count, initializer=worker._init_worker, initargs=(use_gpu,)`
    - Wrap in context manager: `with Pool(...) as pool:`
    - Iterate `pool.imap_unordered(worker.process_image, paths, chunksize=1)`
    - Wrap iterator with `tqdm(total=len(paths), desc="Processing images")`
    - Update progress: `for result in tqdm_wrapper: yield result`
    - Ensure pool closes cleanly (context manager handles this)
  - Document in module docstring: "This module must be called after multiprocessing.set_start_method('spawn') in __main__.py"
- **Verification:**
  - `pytest tests/test_processor.py::test_warmup_models` — runs without error
  - `pytest tests/test_processor.py::test_process_all_small_batch` — process 5 test images, returns 5 results
  - `pytest tests/test_processor.py::test_progress_display` — tqdm updates during iteration (manual inspection)
  - Check tqdm output is clean (not garbled/duplicated) during small batch run
- **Done When:**
  - Warmup step executes and pre-populates caches
  - Pool spawns correct number of workers
  - Results flow as completion order (imap_unordered)
  - Progress bar updates smoothly without corruption
  - No buffering of all results (generator yields as they arrive)

---

### Wave 3: Integration (depends on Core components)

#### Task 3.1: Create writer.py module for incremental output
- **Scope:** Incremental Markdown file writing, crash-safety, UTF-8 encoding
- **Files Modified/Created:**
  - `src/ocr_batch/writer.py` (new)
- **Dependencies:** stdlib only (pathlib, io), models.ProcessResult
- **Time Estimate:** 18–22 min
- **Critical Pitfall Mitigations:**
  - **S4 (UTF-8 encoding):** Always open with `encoding="utf-8"` explicitly.
  - **M8 (header collisions):** Use full filename in headers (e.g., `# image.jpg`).
- **Action:**
  - Implement `OutputWriter` class
    - `__init__(output_path: Path, original_paths: list[Path])`
      - Pre-compute index map: `{path: original_index}` for optional sorting
      - Open output file in append mode, write UTF-8
    - `write_result(result: ProcessResult) -> bool`
      - If result.markdown is None, return False (skipped, not written)
      - If result.markdown is not None, append to file:
        ```
        # {result.path.name}

        {result.markdown}

        ---

        ```
      - Flush after each write (crash-safe)
      - Return True if written, False if skipped
    - `get_counts() -> dict` with keys: `processed`, `skipped`
- **Verification:**
  - `pytest tests/test_writer.py::test_utf8_encoding` — output file is valid UTF-8
  - `pytest tests/test_writer.py::test_incremental_writes` — write 10 results, file has all 10
  - `pytest tests/test_writer.py::test_crash_safety` — kill writer mid-write, file has valid partial content
  - `pytest tests/test_writer.py::test_full_filename_header` — headers use full filename, not stem
- **Done When:**
  - File is created with UTF-8 encoding
  - Results append without buffering
  - Each write followed by flush
  - Full filename in headers
  - Counts tracked correctly

#### Task 3.2: Create cli.py module with Typer CLI
- **Scope:** Argument parsing, validation, orchestration, summary printing
- **Files Modified/Created:**
  - `src/ocr_batch/cli.py` (new)
- **Dependencies:** typer, rich, stdlib, all core modules
- **Time Estimate:** 25–30 min
- **Critical Pitfall Mitigations:**
  - **M3 (Docling DEBUG flood):** Set logging levels before any converter construction (at module import).
- **Action:**
  - Import Typer and create app: `app = typer.Typer(help="Batch OCR processor")`
  - Implement `main()` command:
    ```
    @app.command()
    def main(
        input_dir: str = typer.Argument(..., help="Input folder with images"),
        output: str = typer.Option("output.md", help="Output Markdown file"),
        workers: int = typer.Option(None, help="Number of worker processes (default: 4)"),
        gpu: bool = typer.Option(False, help="Use GPU for OCR"),
        log: str = typer.Option("errors.jsonl", help="Error log file"),
        verbose: bool = typer.Option(False, help="Verbose logging"),
    ) -> int
    ```
  - Validation:
    - Check input_dir exists and is a directory
    - Check input_dir is readable
    - Check output path is writable (or create directory if needed)
  - Setup logging:
    - Set `logging.getLogger("docling").setLevel(logging.WARNING)`
    - Set `logging.getLogger("easyocr").setLevel(logging.WARNING)`
    - If verbose: set to DEBUG instead
  - Orchestration:
    - `paths = discovery.discover_images(Path(input_dir))`
    - Print: f"Found {len(paths)} images to process"
    - `valid_paths, skipped_paths = discovery.validate_images(paths)`
    - Print: f"Skipped {len(skipped_paths)} corrupted/oversized files"
    - Create ErrorLogger instance
    - Create OutputWriter instance
    - Iterate `processor.process_all(valid_paths, worker_count=workers, use_gpu=gpu):`
      - For each result:
        - If result.error: write to error log
        - Else: write to output file
    - Print summary:
      ```
      Processed: {writer.counts()['processed']}
      Skipped: {len(skipped_paths)}
      Failed: {error_count}
      Duration: {elapsed_time}
      Errors logged to: {log}
      ```
    - Exit with code 0 if no errors, else 1
- **Verification:**
  - `pytest tests/test_cli.py::test_help` — `main --help` prints help text
  - `pytest tests/test_cli.py::test_missing_input_dir` — fails with error if input_dir doesn't exist
  - `pytest tests/test_cli.py::test_full_run` — process a small folder, produces output.md and errors.jsonl
  - `pytest tests/test_cli.py::test_exit_code_success` — exit code 0 when no failures
  - `pytest tests/test_cli.py::test_exit_code_failure` — exit code 1 when failures occur
- **Done When:**
  - Typer CLI parses arguments correctly
  - Help text generated automatically
  - Input validation prevents bad paths
  - Orchestration flow completes without deadlock
  - Summary printed to console
  - Exit code set correctly

#### Task 3.3: Create __main__.py entry point
- **Scope:** Module-level entry point, multiprocessing guard
- **Files Modified/Created:**
  - `src/ocr_batch/__main__.py` (new)
- **Dependencies:** multiprocessing, cli module
- **Time Estimate:** 5–8 min
- **Critical Pitfall Mitigations:**
  - **C4 (fork start method):** Set spawn method explicitly before any imports that touch multiprocessing/PyTorch.
  - **C5 (fork bomb):** All Pool construction inside `if __name__ == "__main__":` guard.
- **Action:**
  - Write minimal entry point:
    ```python
    import multiprocessing as mp
    from ocr_batch.cli import app

    if __name__ == "__main__":
        mp.set_start_method("spawn", force=True)
        app()
    ```
  - Ensure this is the ONLY place that sets start_method (to avoid conflicts)
  - Document in comments: "spawn required for PyTorch/Docling safety on macOS"
- **Verification:**
  - `python -m ocr_batch --help` — prints help without error
  - `python -m ocr_batch /tmp/test_images` — runs without fork errors (on macOS)
  - Verify spawn method is set (can check in processor via `multiprocessing.get_start_method()`)
- **Done When:**
  - Module invocation works: `python -m ocr_batch <folder>`
  - Spawn start method explicitly set
  - __main__ guard prevents recursive spawning

---

### Wave 4: Integration Testing & Validation

#### Task 4.1: Create end-to-end integration test
- **Scope:** Full pipeline validation with real images
- **Files Modified/Created:**
  - `tests/test_integration_e2e.py` (new)
  - `tests/fixtures/sample_images/` (create 10 test images: jpg, png, tiff, webp, bmp)
  - One corrupted image for error handling test
- **Dependencies:** pytest, all modules
- **Time Estimate:** 30–40 min
- **Action:**
  - Create pytest fixture: `sample_image_folder` with 10 valid images + 1 corrupted image
  - Test `test_end_to_end_full_batch`:
    - Run `python -m ocr_batch sample_image_folder --output test_out.md --log test_errors.jsonl`
    - Assert output.md exists and contains all 10 image headers
    - Assert errors.jsonl contains 1 error entry
    - Assert exit code is 1 (failures occurred)
    - Assert summary line printed
  - Test `test_output_format`:
    - Verify markdown headers format: `# filename.ext`
    - Verify separators: `---`
    - Verify UTF-8 encoding
  - Test `test_error_log_format`:
    - Parse errors.jsonl
    - Assert each line is valid JSON
    - Assert each entry has required fields: file, error_type, message, timestamp
  - Test `test_parallel_speed`:
    - Time a 20-image batch with 4 workers vs. 1 worker
    - Assert 4-worker run is faster (rough check: within 2x of sequential is acceptable)
  - Test `test_progress_bar_output`:
    - Capture stderr during run
    - Verify progress bar appears (contains tqdm markers)
    - Verify no duplicated output
- **Verification:**
  - All tests pass
  - Output file format is correct
  - Error log is valid JSONL
  - Exit codes reflect success/failure
  - Progress bar renders cleanly
- **Done When:**
  - End-to-end test passes on target OS (macOS/Windows)
  - Output matches specification
  - All success criteria validated

#### Task 4.2: Test multiprocessing safety on target OS
- **Scope:** Verify no crashes, hangs, or fork bombs on macOS/Windows
- **Files Modified/Created:**
  - `tests/test_multiprocessing_safety.py` (new)
- **Dependencies:** pytest, all modules, signal/timeout for hang detection
- **Time Estimate:** 20–25 min
- **Action:**
  - Test `test_no_fork_crashes` (macOS specific):
    - Run processor on 10 images with spawn method
    - Assert all workers complete (no exit code -11 / SIGSEGV)
    - Assert all results have valid data or error (not corrupted)
  - Test `test_no_fork_bomb`:
    - Set process limit alarm (e.g., max 50 processes)
    - Run processor
    - Assert process count stays under limit
    - Assert no "resource exhausted" error
  - Test `test_model_cache_race`:
    - Delete model cache directories
    - Run processor on 5 images
    - Assert models download without corruption
    - Assert errors.jsonl is empty or has only image-specific errors (not cache errors)
  - Test `test_pool_cleanup`:
    - Run processor, complete normally
    - Assert all worker processes terminated
    - Assert no zombie processes
- **Verification:**
  - Tests pass on macOS (primary test environment)
  - Tests pass on Windows (if available)
  - No hangs, crashes, or resource exhaustion
- **Done When:**
  - All multiprocessing safety tests pass
  - Confirmed spawn method prevents fork issues
  - Confirmed no fork bombs with __main__ guard

#### Task 4.3: Validate all 8 success criteria
- **Scope:** Map each success criterion to test evidence
- **Files Modified/Created:**
  - `tests/test_success_criteria.py` (new, collecting evidence)
- **Dependencies:** pytest, all modules
- **Time Estimate:** 20–25 min
- **Action:**
  - **Criterion 1:** User can invoke CLI and receive combined Markdown
    - Evidence: `test_end_to_end_full_batch` output file exists with all image headers
  - **Criterion 2:** Processing across ~300 images in 4 workers (default)
    - Evidence: `test_parallel_speed` shows 4 workers chosen by default, completes in <10 min for 300 images (benchmarked separately, not in test suite; document in README)
  - **Criterion 3:** Failed images logged to errors.jsonl
    - Evidence: `test_error_log_format` validates JSONL structure
  - **Criterion 4:** Progress bar displays without corruption
    - Evidence: `test_progress_bar_output` captures stderr, verifies no duplicates/garbling
  - **Criterion 5:** Workers initialize safely on macOS/Windows with spawn
    - Evidence: `test_no_fork_crashes` confirms no SIGSEGV, all results valid
  - **Criterion 6:** Output written incrementally with UTF-8 and full filenames
    - Evidence: `test_output_format` verifies UTF-8 and headers, `test_crash_safety` verifies incremental writes
  - **Criterion 7:** Summary shows counts and elapsed time; exit codes 0/1
    - Evidence: `test_end_to_end_full_batch` captures exit code and summary output
  - **Criterion 8:** All files discovered, sorted, validated; failures captured
    - Evidence: `test_end_to_end_full_batch` and discovery unit tests confirm sorting and validation
  - Create summary table mapping criteria → test functions
- **Verification:**
  - All 8 criteria have corresponding test evidence
  - Each test passes
  - Documentation links test to criterion
- **Done When:**
  - All 8 success criteria have passing evidence
  - Summary table created and documented

---

## Dependency Graph

```
Discovery.py (Foundation)
    ↓
    ├─→ Logger.py (Foundation, independent)
    ├─→ Worker.py (Core — depends on models.ProcessResult + discovery for types)
    │   ├─→ Processor.py (Core — depends on worker._init_worker)
    │   │   ├─→ Writer.py (Integration — depends on ProcessResult)
    │   │   └─→ CLI.py (Integration — depends on all above)
    │   │       └─→ __main__.py (Entry — depends on cli.app)
    │   │
    │   └─→ E2E Tests (Wave 4, all modules must be complete)
```

**Wave Execution:**
- **Wave 1 (parallel):** discovery.py + logger.py (no dependencies)
- **Wave 2 (after Wave 1):** worker.py + processor.py (depend on discovery types, worker initializer)
- **Wave 3 (after Wave 2):** writer.py + cli.py (depend on ProcessResult from worker + all orchestration)
- **Wave 3.1 (after Wave 3):** __main__.py (depends on cli.py)
- **Wave 4 (after Wave 3.1):** Integration testing (all modules complete)

**File Dependencies (no file conflicts — each task owns exclusive files):**
- Task 1.1: creates `src/ocr_batch/discovery.py`
- Task 1.2: creates `src/ocr_batch/logger.py`
- Task 2.1: creates `src/ocr_batch/worker.py`, `src/ocr_batch/models.py`
- Task 2.2: creates `src/ocr_batch/processor.py`
- Task 3.1: creates `src/ocr_batch/writer.py`
- Task 3.2: creates `src/ocr_batch/cli.py`
- Task 3.3: creates `src/ocr_batch/__main__.py`
- Task 4.x: creates test files (no conflicts)

**All parallel within waves, sequential between waves.**

---

## Risk Mitigations Summary

### Critical Pitfalls (C1-C5) — Embedded in Tasks

| Pitfall | Mitigation | Task | Evidence |
|---------|-----------|------|----------|
| **C1: DocumentConverter not picklable** | `_init_worker()` constructs converter once per worker, stored in global `_converter` | Task 2.1 | `test_init_worker` passes; converter reused across calls |
| **C2: Model download race** | `warmup_models()` pre-populates caches before Pool creation | Task 2.2 | `test_warmup_models` passes; no concurrent download errors |
| **C3: EasyOCR init not thread-safe** | Same warmup step (C2) prevents concurrent initialization | Task 2.2 | `test_warmup_models` passes; no permission errors on cache |
| **C4: Fork start method crashes PyTorch** | Set `multiprocessing.set_start_method("spawn", force=True)` in `__main__.py` | Task 3.3 | `test_no_fork_crashes` passes; no SIGSEGV on macOS |
| **C5: Fork bomb without __main__ guard** | All Pool construction inside `if __name__ == "__main__":` | Task 3.3 | `test_no_fork_bomb` passes; process count stays under limit |

### Significant Pitfalls (S1-S7) — Embedded in Tasks

| Pitfall | Mitigation | Task | Evidence |
|---------|-----------|------|----------|
| **S1: OOM from buffering** | Use `pool.imap_unordered()` + incremental file writes | Task 2.2, 3.1 | `test_crash_safety` passes; output written as results arrive |
| **S2: tqdm garbling** | Single progress bar in main process wrapping imap_unordered | Task 2.2 | `test_progress_bar_output` captures stderr; no duplicates |
| **S3: Non-deterministic output order** | Document completion-order output; sort by index if needed | Task 3.1 | Documented in README; MVP accepts completion order |
| **S4: UTF-8 encoding issues** | Always `open(..., encoding="utf-8")` | Task 3.1 | `test_utf8_encoding` passes; valid UTF-8 output |
| **S5: Corrupted images hang** | Pre-validate with `PIL.Image.open().verify()` | Task 1.1 | `test_validate_images` skips corrupted files |
| **S6: Large images OOM workers** | Check dimensions; skip if > 25 MP | Task 1.1 | `test_validate_images` skips oversized files |
| **S7: Worker count ignores RAM** | Default to `min(cpu_count, 4)` | Task 2.2 | `test_process_all_small_batch` uses correct default |

### Minor Pitfalls (M1-M8) — Embedded in Tasks

| Pitfall | Mitigation | Task | Evidence |
|---------|-----------|------|----------|
| **M3: Docling DEBUG flood** | Set `logging.getLogger("docling").setLevel(logging.WARNING)` | Task 3.2 | `test_full_run` runs with clean stderr (no DEBUG lines) |
| **M4: Unsorted glob results** | Use `natsort.natsorted()` | Task 1.1 | `test_natural_sort` validates img1, img2, img10 order |
| **M5: Silent failures** | Worker returns ProcessResult with error fields always set | Task 2.1 | `test_process_image_error` confirms error state |
| **M6: WEBP codec missing** | Check `PIL.features.check_codec("webp")` at startup | Task 1.1 | `test_check_webp_support` verifies availability |
| **M8: Header collisions** | Use full filename in headers: `# image.jpg` | Task 3.1 | `test_full_filename_header` validates format |

---

## Success Criteria Mapping

Each of the 8 success criteria maps to specific tasks and test evidence:

| Criterion | Description | Tasks | Verification Test |
|-----------|-------------|-------|-------------------|
| **1** | User can invoke CLI and receive combined Markdown | 3.2, 3.3, 4.1 | `test_end_to_end_full_batch` validates output.md exists with all headers |
| **2** | Processing across 300 images in parallel (4 workers default) | 2.2, 3.2 | `test_parallel_speed` confirms 4 workers chosen; throughput acceptable |
| **3** | Failed images logged to errors.jsonl | 1.2, 2.1, 3.2, 4.1 | `test_error_log_format` validates JSONL with all required fields |
| **4** | Progress bar displays live without corruption | 2.2, 4.1 | `test_progress_bar_output` captures stderr; no duplicates/garbling |
| **5** | Workers initialize safely on macOS/Windows with spawn | 3.3, 2.1, 4.2 | `test_no_fork_crashes` confirms no SIGSEGV; all workers healthy |
| **6** | Output written incrementally (crash-safe) with UTF-8 and full filenames | 3.1, 4.1 | `test_output_format`, `test_crash_safety` validate format and incremental writes |
| **7** | Final summary shows counts, elapsed time; exit code 0/1 | 3.2, 4.1 | `test_end_to_end_full_batch` captures summary and exit code |
| **8** | All files discovered, sorted, validated; failures captured with context | 1.1, 4.1 | `test_discover_images`, `test_natural_sort`, `test_validate_images` |

---

## Open Questions & Verification Points

These will be addressed during implementation (Wave 1-3) and confirmed in integration testing (Wave 4):

| Question | How to Verify | Owner Task | Evidence |
|----------|---------------|-----------|----------|
| **Docling v2 API signatures** | Check installed docling version after `uv add docling` | Task 2.1 | `test_init_worker` passes; converter instantiates correctly |
| **DocumentConverter memory behavior** | Monitor RSS during 20-image run | Task 4.1 | Memory profile collected; no unbounded growth |
| **Optimal worker formula** | Benchmark 5/10/20/300-image runs with workers=1,2,4,8 | Task 4.1 | Throughput graph created; default 4 chosen |
| **Model warmup effectiveness** | Compare first run (cold cache) vs. second run (warm cache) | Task 2.2 | Times logged; cold cache time reasonable |
| **tqdm + imap_unordered compat** | Render progress bar live during test | Task 2.2 | `test_progress_bar_output` validates output |
| **Incremental write crash safety** | Kill process mid-write, verify file is valid | Task 3.1 | `test_crash_safety` simulates interruption |
| **Multiprocessing spawn safety on target OS** | Run full batch on macOS, verify no crashes | Task 4.2 | `test_no_fork_crashes` passes; process log clean |

---

## Success Criteria Summary

**All 8 criteria must be TRUE at Phase 1 completion:**

1. ✓ CLI invocation with folder path → combined Markdown output
2. ✓ Parallel completion across 300 images with 4 workers (default)
3. ✓ Failed images logged to errors.jsonl (filename, type, timestamp)
4. ✓ Progress bar displays live without visual corruption
5. ✓ Workers initialize safely on macOS/Windows using spawn + non-picklable converter per-worker
6. ✓ Output written incrementally (crash-safe), UTF-8 encoding, full filenames in headers
7. ✓ Summary: processed/skipped/failed counts + elapsed time; exit code 0 or 1
8. ✓ All files discovered, naturally sorted, validated; failures captured with full context

---

## Artifact Checklist

At Phase 1 completion, these artifacts must exist:

**Source Files:**
- [ ] `src/ocr_batch/__init__.py`
- [ ] `src/ocr_batch/discovery.py` (REQ-02, REQ-03, REQ-04, REQ-05)
- [ ] `src/ocr_batch/logger.py` (REQ-16, REQ-17, REQ-18)
- [ ] `src/ocr_batch/models.py` (ProcessResult dataclass)
- [ ] `src/ocr_batch/worker.py` (REQ-06, REQ-09, REQ-10)
- [ ] `src/ocr_batch/processor.py` (REQ-07, REQ-08)
- [ ] `src/ocr_batch/writer.py` (REQ-11, REQ-12, REQ-13, REQ-14, REQ-15)
- [ ] `src/ocr_batch/cli.py` (REQ-01, REQ-21, REQ-22, REQ-23, REQ-24, REQ-25)
- [ ] `src/ocr_batch/__main__.py` (module entry point)

**Tests:**
- [ ] `tests/test_discovery.py`
- [ ] `tests/test_logger.py`
- [ ] `tests/test_worker.py`
- [ ] `tests/test_processor.py`
- [ ] `tests/test_writer.py`
- [ ] `tests/test_cli.py`
- [ ] `tests/test_integration_e2e.py`
- [ ] `tests/test_multiprocessing_safety.py`
- [ ] `tests/test_success_criteria.py`

**Test Fixtures:**
- [ ] `tests/fixtures/sample_images/` (10 valid images + 1 corrupted)

**All 25 Requirements (REQ-01 to REQ-25) Covered:**
- [ ] All requirements appear in at least one task action or verification
- [ ] No requirement left unaddressed

---

## Notes for Executor

1. **Wave Execution:** Each wave must complete fully before the next begins. Tasks within a wave can run in parallel but have no dependencies on each other.

2. **Pitfall Mitigations:** The red-flagged critical/significant pitfalls (C1-C5, S1-S7, M3-M8) are deeply embedded in specific tasks. Do not skip or defer these mitigations — they directly prevent runtime failures.

3. **Testing Early:** Each task includes unit tests. Run them immediately after task completion; do not defer testing to Wave 4.

4. **Integration Testing (Wave 4):** Requires all modules from Waves 1-3 to be complete. It is the final validation gate before shipping.

5. **Open Questions:** Flag any API mismatches (e.g., Docling v2 signature unexpected) immediately in Wave 2 and adjust task actions.

6. **Success Criteria Validation:** Task 4.3 creates the final evidence table. Cross-check each criterion against test output before claiming Phase 1 complete.

---

*Plan created: 2026-04-28 | Phase 1: Multiprocessing Foundation + Core Pipeline*
