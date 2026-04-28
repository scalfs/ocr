# Architecture — OCR Batch Processor

**Domain:** Python CLI batch image OCR tool
**Researched:** 2026-04-28
**Confidence:** MEDIUM-HIGH — based on Docling v2 documentation patterns and Python multiprocessing best practices. No live docs fetched (network tools unavailable); grounded in training knowledge through Aug 2025.

---

## Components

### 1. CLI Entry Point (`cli.py`)

**What it owns:**
- Argument parsing (`argparse` or `click`): `--input-dir`, `--output`, `--workers`, `--log`
- Input validation: directory exists, is readable, output path is writable
- Orchestration: calls File Discovery, then Batch Processor, then Output Writer
- Exit codes: 0 on success, 1 on partial failure (some errors), 2 on total failure

**Does NOT own:** any OCR logic, file I/O of results, or worker management.

**Boundary:** receives `Namespace` args, delegates everything, prints summary to stdout.

---

### 2. File Discovery (`discovery.py`)

**What it owns:**
- Glob scanning of a single flat directory (no recursion — out of scope)
- Extension filtering: `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif`, `.webp`, `.bmp`
- Case-insensitive extension matching (`.JPG` and `.jpg` both valid)
- Returns sorted `list[Path]`

**Does NOT own:** OCR, output, error logging.

**Key detail:** Returns a concrete list (not generator) so the caller knows total count for progress tracking before processing starts.

```python
def discover_images(folder: Path) -> list[Path]:
    extensions = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp", ".bmp"}
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in extensions)
```

---

### 3. Worker Initializer (`worker.py`)

**What it owns:**
- `_init_worker()`: run once per worker process via `Pool(initializer=...)` — loads `DocumentConverter` with EasyOCR pipeline into a module-level global
- `process_image(path: Path) -> ProcessResult`: calls the pre-loaded converter on a single image, returns structured result

**Why this matters:** EasyOCR and Docling both load PyTorch models on initialization. Loading them per-call would re-read ~500MB of model weights every call. Loading once per worker amortizes this cost across all images that worker handles.

**ProcessResult dataclass:**
```python
@dataclass
class ProcessResult:
    path: Path
    markdown: str | None   # None on failure
    error: str | None      # None on success
    error_type: str | None # exception class name
    duration_s: float
```

**Does NOT own:** parallelism coordination, file writing, discovery.

---

### 4. Batch Processor (`processor.py`)

**What it owns:**
- `multiprocessing.Pool` lifecycle: create with `initializer=_init_worker`, map images to workers
- Worker count selection: `min(os.cpu_count() or 4, args.workers or 4)` — default 4, cap at cpu_count
- Chunking: feeds `pool.imap_unordered(process_image, paths, chunksize=1)` — chunksize=1 ensures even distribution (images vary in complexity)
- Progress bar: wraps imap_unordered with `tqdm(total=len(paths))`
- Yields `ProcessResult` as results complete

**Does NOT own:** model loading (worker owns that), file writing (output writer owns that).

**Key design:** `imap_unordered` returns results as they complete rather than in submission order. This maximizes throughput and allows the output writer to start writing immediately. Final output ordering is handled by the output writer if determinism is needed (sort by original path index).

---

### 5. Output Writer (`writer.py`)

**What it owns:**
- Receives stream of `ProcessResult` from batch processor
- Writes to a single `.md` file: `# {filename}\n\n{markdown}\n\n---\n\n` per image
- Maintains insertion order (maps path → index from discovery list, writes in that order OR appends in completion order — see Key Decisions)
- Flushes after each entry (safe against crash mid-run)
- Reports count of successes and failures at end

**Does NOT own:** OCR, error log (that is the Error Logger's job).

**Thread safety:** The main process is the sole writer. No locks needed.

---

### 6. Error Logger (`logger.py`)

**What it owns:**
- Writes a `.jsonl` (JSON Lines) error log file — one JSON object per failed image
- Each entry: `{"file": "img.jpg", "error_type": "ValueError", "message": "...", "timestamp": "2026-04-28T12:00:00Z"}`
- Flushes after each write (don't buffer errors — if process crashes, partial log is valid)
- JSONL chosen over CSV: structured, no quoting complexity, trivially parseable

**Does NOT own:** what constitutes an error (that comes from ProcessResult).

---

## Data Flow

```
[CLI args]
    │
    ▼
[File Discovery]
    │  list[Path] (sorted, filtered, concrete)
    ▼
[Batch Processor]
    │  spawns Pool(workers=N, initializer=_init_worker)
    │  each worker: loads DocumentConverter + EasyOCR once
    │  feeds imap_unordered(process_image, paths, chunksize=1)
    │
    │  per image (in worker process):
    │    converter.convert(path, raises_on_error=False)
    │    → ConversionResult → .document.export_to_markdown()
    │    → ProcessResult(path, markdown, error, duration)
    │
    │  ProcessResult stream (unordered, as completed)
    ▼
[Main Process — result loop]
    │
    ├──► [Output Writer] → appends to output.md
    │
    ├──► [Error Logger] → appends to errors.jsonl (failures only)
    │
    └──► [tqdm progress bar] → stderr
    │
    ▼
[CLI] prints summary: N succeeded, M failed, see errors.jsonl
```

---

## Parallelism Strategy

### convert_all() vs multiprocessing.Pool

**Use `multiprocessing.Pool` with `process_image()` per image. Do NOT rely on `convert_all()` alone for parallelism.**

Rationale:
- `convert_all()` is a convenience batch API that iterates documents through a single pipeline in **one process**. It does not spawn workers. It uses an internal thread pool for I/O overlap but OCR (EasyOCR/PyTorch) releases the GIL during C extension inference, so threads work, but process-level isolation is cleaner and avoids shared model state issues.
- `multiprocessing.Pool` gives true process-level parallelism: each worker has its own Python interpreter, memory space, and model copies. No GIL contention for pure Python code.
- `convert_all()` IS useful inside a single worker: a worker assigned a sub-batch can call `converter.convert_all(sub_batch)` to process its chunk sequentially. But for 300 images split across N workers, it's simpler to call `converter.convert(single_image)` per task and let Pool manage distribution.

**Recommendation:** `Pool.imap_unordered(process_image, all_paths, chunksize=1)` where `process_image` calls `converter.convert(path)` on the pre-loaded converter.

### Worker Count

| Resource | Constraint | Recommendation |
|----------|-----------|----------------|
| CPU cores | Primary bound for CPU-bound OCR | `min(cpu_count, requested)` |
| RAM | EasyOCR English model ~400-600MB per worker | Max 4 workers on 4GB free RAM, 6-8 on 8GB+ |
| GPU | If CUDA available, 1-2 GPU workers dominate | Detect and adjust |

**Safe default: 4 workers.** This fits on a MacBook with 16GB RAM. Allow override via `--workers N` CLI flag.

### Model Loading

- **Load once per worker process** via `Pool(initializer=_init_worker, initargs=())`
- `_init_worker` instantiates `DocumentConverter` with EasyOCR pipeline options — stored in a module-level global
- Each call to `process_image()` in that worker reuses the already-loaded model
- Cost: N workers × model load time (10-30s first call, cached after) at startup
- Benefit: 300 images processed without reloading models

**Do NOT** instantiate `DocumentConverter` inside `process_image()` — this would reload models on every image.

**Fork safety:** Use `Pool` with `spawn` start method on macOS/Python 3.11+ (spawn is the default on macOS since Python 3.8). `fork` is unsafe with PyTorch and EasyOCR. Explicitly set: `multiprocessing.set_start_method("spawn")` in `if __name__ == "__main__"` guard.

### Chunking Strategy

- `chunksize=1`: each worker picks up one image at a time
- OCR time per image varies widely (dense text vs blank image = 10x difference)
- `chunksize=1` prevents a worker sitting idle while another processes a large batch
- For 300 images / 4 workers, overhead of individual task dispatch is negligible

---

## Build Order

Dependencies flow top to bottom. Build each layer before the layer that depends on it.

```
Phase 1: Foundation
  └── discovery.py        (no deps — pure stdlib glob)
  └── logger.py           (no deps — stdlib json, datetime)

Phase 2: Core Processing
  └── worker.py           (deps: docling, discovery types)
  └── processor.py        (deps: worker.py, multiprocessing, tqdm)

Phase 3: Output
  └── writer.py           (deps: worker ProcessResult type)

Phase 4: Integration
  └── cli.py              (deps: discovery, processor, writer, logger)
  └── __main__.py         (deps: cli — enables `python -m ocr_batch`)
```

**Why this order:**
- Discovery is pure stdlib — verifiable in isolation before OCR is wired in
- Logger is pure stdlib — test error log format independently
- Worker must exist before Processor (Processor just manages the Pool, Worker owns the OCR)
- Writer depends only on the result shape (ProcessResult) — can be tested with mock results
- CLI is last — it composes all others; integration test is the final validation

---

## Key Technical Decisions

### 1. Model Loading: Once Per Worker via initializer

**Decision:** `DocumentConverter` is instantiated in `_init_worker()`, stored as a module global, reused across all calls in that worker.

**Why:** EasyOCR loads PyTorch models from disk on instantiation. Loading per-call on 300 images would repeat a 15-30s model load 300 times. Loading once per worker amortizes startup across all images that worker handles.

**Risk:** If Docling's `DocumentConverter` is not stateless across calls (e.g., internal caching that grows unbounded), memory per worker may grow. Monitor with a small run first.

### 2. Spawn Start Method (Not Fork)

**Decision:** Explicitly use `"spawn"` multiprocessing start method.

**Why:** macOS Python 3.8+ defaults to `spawn`, but it's worth being explicit. `fork` with PyTorch/EasyOCR causes deadlocks or crashes due to CUDA context and non-fork-safe internal state. `spawn` starts a clean process and runs the initializer fresh.

**Implementation:** `multiprocessing.set_start_method("spawn")` inside `if __name__ == "__main__":` before `Pool` creation.

### 3. imap_unordered for Throughput

**Decision:** `pool.imap_unordered()` not `pool.map()`.

**Why:** `pool.map()` collects all results before returning — with 300 images, this blocks the writer until everything is done. `imap_unordered` yields results as they complete, allowing the writer to flush completed results to disk immediately. Output file grows progressively; a crash mid-run preserves work done so far (minus the incomplete current entry).

**Tradeoff:** Output order is non-deterministic (completion order, not input order). Two options:
- Accept completion order (simpler, good enough for grep/review use case)
- Buffer and reorder by original index in main loop (adds memory overhead, adds complexity)

**Recommendation:** Accept completion order for the MVP. Document it clearly. Add sorting as opt-in if requested.

### 4. Error Handling: raises_on_error=False + ProcessResult

**Decision:** Pass `raises_on_error=False` to Docling and catch all exceptions in `process_image()`. Return a `ProcessResult` with `error` set rather than raising.

**Why:** A single bad image (corrupted, wrong format masquerading as valid extension, zero-byte file) must not kill the worker process. The worker must stay alive for subsequent images. Errors are captured as data, not exceptions that propagate to Pool.

**What to capture in error log:**
- `file`: relative filename (not full path — portable)
- `error_type`: `type(e).__name__` (e.g., `"ValueError"`, `"RuntimeError"`)
- `message`: `str(e)` truncated to 500 chars
- `timestamp`: ISO 8601 UTC
- `duration_s`: how long the attempt took before failing

### 5. Output File: Append with Flush, Not Buffer-All

**Decision:** Open output file in append mode, write each result as it arrives, flush after each entry.

**Why:** Buffering 300 results in memory before writing risks OOM on large images with verbose markdown output. Append-with-flush gives crash-safety: a run that dies at image 250 has a valid output file with 249 entries.

**Format per entry:**
```markdown
# image_filename.jpg

[extracted text here]

---

```

The `---` separator makes the file scannable and each section visually distinct.

### 6. Docling EasyOCR Pipeline Configuration

**Decision:** Configure `DocumentConverter` with `EasyOcrOptions` specifying English only.

**Why:** Loading EasyOCR with only the needed language avoids downloading and loading unnecessary language model weights (each language adds ~50-200MB).

```python
from docling.datamodel.pipeline_options import EasyOcrOptions
from docling.document_converter import DocumentConverter, ImageFormatOption
from docling.pipeline.simple_pipeline import SimplePipelineOptions

ocr_options = EasyOcrOptions(lang=["en"], use_gpu=False)
pipeline_options = SimplePipelineOptions(ocr_options=ocr_options)
converter = DocumentConverter(
    format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options)}
)
```

**GPU:** Default to `use_gpu=False` for portability. Add `--gpu` CLI flag to enable. GPU mode with 1-2 workers will outperform CPU mode with 4 workers on supported hardware.

---

## Component Boundaries Summary

| Component | Input | Output | External Deps |
|-----------|-------|--------|---------------|
| `discovery.py` | `Path` (folder) | `list[Path]` | stdlib only |
| `logger.py` | `ProcessResult` (failed) | `.jsonl` file | stdlib only |
| `worker.py` | `Path` (image) | `ProcessResult` | docling, easyocr |
| `processor.py` | `list[Path]`, worker_count | stream of `ProcessResult` | multiprocessing, tqdm |
| `writer.py` | stream of `ProcessResult` | `.md` file | stdlib only |
| `cli.py` | `sys.argv` | exit code | all above modules |

---

## Sources

- Docling v2 documentation patterns (training knowledge, HIGH confidence for API shape)
- Python `multiprocessing` documentation — spawn vs fork, Pool initializer pattern (HIGH confidence)
- EasyOCR model size / loading behavior (MEDIUM confidence — based on known PyTorch model loading patterns)
- `convert_all()` as single-process batch API, not a parallelism primitive (MEDIUM confidence — verify against current Docling source before implementing)

**Flag for phase research:** Verify exact `DocumentConverter` + `EasyOcrOptions` instantiation signature against installed Docling version before coding `worker.py`. API surface has changed between Docling v1 and v2.
