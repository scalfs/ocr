# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run the batch processor
uv run python -m ocr_batch /path/to/images --output output.md --log errors.jsonl

# Run with local model caches (recommended for dev — keeps downloads inside project)
mkdir -p .cache/easyocr .cache/docling .cache/hf
EASYOCR_MODULE_PATH="$PWD/.cache/easyocr" \
DOCLING_CACHE_DIR="$PWD/.cache/docling" \
HF_HOME="$PWD/.cache/hf" \
uv run python -m ocr_batch /path/to/images --output output.md --log errors.jsonl --workers 1

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_discovery.py

# Run a single test
uv run pytest tests/test_discovery.py::TestDiscoverImages::test_natural_sort_order
```

## Architecture

The package lives in `src/ocr_batch/` and is invoked as `python -m ocr_batch`. Six modules with strict ownership boundaries:

| Module | Owns | External deps |
|--------|------|---------------|
| `discovery.py` | Glob scan, case-insensitive extension filter, natsort, PIL pre-validation (corruption + 25 MP limit) | stdlib + natsort + pillow |
| `models.py` | `ProcessResult` dataclass (path, markdown, error, error_type, duration_s) | stdlib |
| `worker.py` | `_init_worker()` + `process_image()` — one `DocumentConverter` instance per worker process stored as module global. OCR backend: **RapidOCR** (onnxruntime, CPU, ~1.6s/image after warmup). `gc.collect()` called after each conversion to slow memory growth. | docling, rapidocr, onnxruntime |
| `processor.py` | `multiprocessing.Pool` lifecycle, warmup step, `imap_unordered` result streaming, tqdm in main process | multiprocessing, tqdm |
| `writer.py` | Incremental append-mode writes to `.md` (UTF-8, flush after each entry) | stdlib |
| `logger.py` | Append-only JSONL error log, flush after each entry | stdlib |
| `cli.py` | Typer CLI, validation, orchestration, summary | typer, rich |
| `__main__.py` | Sets `mp.set_start_method("spawn")` before anything else | — |

### Data flow

```
CLI args → discover_images() → validate_images()
  → warmup_models()
  → Pool(initializer=_init_worker)
  → imap_unordered(process_image, paths, chunksize=1)
  → main process loop:
      success → OutputWriter.write_result()   → output.md
      failure → ErrorLogger.log_error()       → errors.jsonl
      all     → tqdm progress bar             → stderr
```

### Critical design constraints

**`DocumentConverter` is not picklable.** It cannot be passed to workers. Each worker must build its own instance. The `_init_worker()` initializer pattern in `worker.py` is the only safe approach — do not change it.

**`spawn` start method is mandatory.** Set in `__main__.py` before any other code runs. Using `fork` with PyTorch/EasyOCR causes silent crashes or deadlocks (especially on macOS). The `if __name__ == "__main__":` guard in `__main__.py` is also required to prevent fork bombs under `spawn`.

**Warmup before pool.** `processor.warmup_models()` runs a single-process `DocumentConverter` init before `Pool` creation to pre-populate `~/.cache/docling` and `~/.EasyOCR/model/`. Without this, N workers race to download the same model files simultaneously and corrupt them.

**Worker count capped at 4.** Each Docling+EasyOCR worker needs ~800 MB–1.5 GB RAM. Default is `min(cpu_count, 4)` to avoid OOM.

**`imap_unordered` with `chunksize=1`.** OCR time per image varies 10x. `chunksize=1` ensures even load distribution. Results arrive in completion order (not input order) — the output file reflects this. `pool.map()` would buffer all results in memory before returning.

**Logging suppression before converter instantiation.** `setup_logging()` in `cli.py` sets `docling` and `easyocr` loggers to WARNING before any converter is created. Call order matters — if logging is set up after, Docling floods stderr with DEBUG during model load.

### Output format

Each successfully processed image appends to the output `.md` as:
```
# filename.ext

[extracted markdown text]

---

```

Full filename including extension is used as the header (not stem) to avoid collisions when `scan.jpg` and `scan.png` coexist.

### Error log format

Each failed image appends one JSON line to the JSONL log:
```json
{"file": "image.jpg", "error_type": "ConversionError", "message": "...", "timestamp": "2026-04-28T12:00:00Z", "duration_s": 1.23}
```

### Known limitations

- Flat directory only — no recursive scan.
- Multi-page TIFF files: only the first frame is extracted.
- Images > 25 MP are skipped during pre-validation (logged to JSONL as skipped).
- First run downloads ~500 MB of model weights; use the cache env vars above to keep them inside the project.
