# Pitfalls — OCR Batch Processor

**Domain:** Python batch OCR — Docling + EasyOCR + multiprocessing + UV
**Researched:** 2026-04-28
**Confidence note:** External tool access (WebSearch, WebFetch, Bash) was unavailable during this research session. All findings are drawn from training knowledge of Docling v2, EasyOCR, Python multiprocessing, and UV as of mid-2025. Mark all claims MEDIUM confidence; validate against current Docling GitHub issues and EasyOCR docs before implementation.

---

## Critical (will break the tool)

### C1: DocumentConverter is NOT safe to share across processes

**What goes wrong:** `DocumentConverter` initializes PyTorch models, ONNX runtime sessions, and internal thread pools during `__init__`. These objects contain C-extension handles, file descriptors, and CUDA contexts that cannot be serialized by `pickle`. Passing a converter instance to a `multiprocessing.Pool` worker (via `initializer` arg or as a task argument) raises `PicklingError` or silently produces corrupted state.

**Warning sign:** `AttributeError: Can't pickle local object`, `TypeError: cannot pickle 'torch._C._TensorBase' object`, or workers that appear to start but immediately die with exit code -11 (segfault).

**Root cause:** Python's `multiprocessing` with `spawn` or `forkserver` start methods (mandatory on macOS and Windows) requires every object sent to workers to be picklable. Heavy ML objects never are.

**Prevention:** Each worker process must construct its own `DocumentConverter` instance. Use `multiprocessing.Pool` with an `initializer` function that builds the converter and stores it in a module-level or `global` variable inside the worker. Do NOT pass the converter as a task argument.

```python
# Pattern: initializer-based converter per worker
_converter = None

def _init_worker():
    global _converter
    _converter = DocumentConverter(...)  # built fresh per process

def _process_one(image_path):
    return _converter.convert(image_path)

with Pool(n, initializer=_init_worker) as pool:
    results = pool.map(_process_one, paths)
```

**Phase:** Core implementation (Phase 1 / worker setup). Get this right before writing any other parallel logic.

---

### C2: Model download on first run blocks silently and can fail mid-batch

**What goes wrong:** Docling downloads layout analysis models (docling-models, TableFormer) and EasyOCR downloads language detection + recognition weights (~100–400 MB total) on first invocation if the cache directories are absent. When this happens inside a worker process spawned by `multiprocessing`, multiple workers may attempt simultaneous downloads to the same cache path, causing partial writes, corrupted model files, or HTTP timeout errors that surface as cryptic `RuntimeError: PytorchStreamReader failed` or `zipfile.BadZipFile`.

**Warning sign:** First run takes 5-10 minutes and then crashes. Subsequent runs may also fail if a model file was partially written during a concurrent download race.

**Cache paths to know:**
- Docling models: `~/.cache/docling/` (or `DOCLING_CACHE_DIR` env var)
- EasyOCR models: `~/.EasyOCR/model/` (or `EASYOCR_MODULE_PATH` env var)

**Prevention:**
1. Add a `--warmup` CLI flag (or auto-detect) that runs a single-process dummy conversion before spawning the pool. This pre-populates all model caches.
2. Document in README that first run requires internet access and ~500 MB of free disk in cache dirs.
3. Alternatively, build the converter once in the main process (just to trigger downloads) before spawning workers — the worker's own `DocumentConverter.__init__` will then find the cached files already present.

**Phase:** Phase 1 (CLI entry point) — warmup step must come before `Pool` construction.

---

### C3: EasyOCR language model initialization is not thread/process safe without isolation

**What goes wrong:** EasyOCR's `Reader` object downloads models and initializes a PyTorch model per language. When multiple processes all hit `easyocr.Reader(['en'])` simultaneously against a cold cache, concurrent writes to `~/.EasyOCR/model/` cause `PermissionError` (Windows) or data corruption (Linux/macOS race on atomic rename). Even with a warm cache, EasyOCR 1.x initializes CUDA contexts per `Reader` instance, and creating N readers in N processes on a single GPU exhausts VRAM immediately.

**Warning sign:** `RuntimeError: CUDA out of memory` on first batch item. Workers crashing with exit code 139 (segfault in CUDA driver). Corrupted `.pth` files in EasyOCR model directory.

**Prevention:**
- Warm the EasyOCR cache before spawning workers (same warmup step as C2).
- On CPU-only machines: N workers each loading a full EasyOCR model is fine for RAM but must be validated.
- On GPU machines: Force EasyOCR to use CPU inside workers (`gpu=False` in `PipelineOptions`) unless you've verified VRAM is sufficient for N parallel GPU contexts. Prefer 1 GPU worker or explicit device assignment.

**Phase:** Phase 1 (worker initializer).

---

### C4: `fork` start method on macOS causes silent crashes with PyTorch/ONNX

**What goes wrong:** Python's default multiprocessing start method on macOS prior to 3.12 was `fork`. PyTorch, ONNX Runtime, and EasyOCR all contain internal state (thread pools, CUDA contexts, file locks) that is not safe to fork. The result is silent data corruption, deadlocks in OpenMP thread pools, or segfaults that produce no Python traceback.

**Warning sign:** Workers hang indefinitely after fork, or process exits with code -6 (SIGABRT) or -11 (SIGSEGV) with no Python traceback. Observed frequently on macOS with PyTorch when `mp.set_start_method('fork')` is used or left as default.

**Prevention:**
```python
import multiprocessing as mp
if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
```
Place this at the very top of the entry point, before any imports that touch PyTorch. `spawn` is slower (each worker is a fresh Python interpreter) but is the only correct method for ML workloads on macOS. The `if __name__ == "__main__":` guard is mandatory with `spawn` to prevent recursive worker spawning.

**Phase:** Phase 1 (entry point) — must be the first thing in `main()`.

---

### C5: UV `uv run` entry point does not propagate `if __name__ == "__main__"` guard correctly without it

**What goes wrong:** With `spawn` multiprocessing on Windows and macOS, worker processes re-import the main module. If the script calls `Pool(...)` at module level (outside `if __name__ == "__main__":`), each worker process spawns more workers, creating a fork bomb that exhausts OS process limits.

**Warning sign:** Hundreds of Python processes spawned instantly. `OSError: [Errno 35] Resource temporarily unavailable` or system freeze.

**Prevention:** All `Pool` construction and `main()` invocation must be inside `if __name__ == "__main__":`. This is standard Python multiprocessing advice but UV users sometimes forget because `uv run script.py` feels like a direct invocation rather than an importable module.

**Phase:** Phase 1 (entry point structure).

---

## Significant (will hurt quality or UX)

### S1: OOM with large image batches when workers hold results in memory

**What goes wrong:** `pool.map()` collects ALL results into memory before returning. With 300 images each producing a result dict, and each Docling result containing the full document object (which may hold intermediate image tensors, bitmaps, and text trees), peak memory = N_workers × model_size + 300 × result_size. On machines with 8–16 GB RAM this can OOM.

**Warning sign:** `MemoryError`, system swap thrashing, or kernel OOM killer terminating the process.

**Prevention:**
- Use `pool.imap_unordered()` and write results to the output file incrementally as they arrive. Do not accumulate all 300 results.
- Free the Docling result object explicitly after extracting the text string: `del result`.
- Docling's `DocumentConverter` with `raises_on_error=False` via `convert_all` returns a generator — use it as such rather than materializing it.

**Phase:** Phase 1 (result collection loop).

---

### S2: tqdm + multiprocessing produces garbled or duplicated progress bars

**What goes wrong:** `tqdm` writes ANSI escape codes to stderr. When multiple worker processes (with their own stderr) run concurrently, their progress output interleaves with the main process's bar, producing visual garbage. Using `tqdm` inside worker functions with `Pool.map` is especially problematic because workers don't share the main process's tqdm state.

**Warning sign:** Multiple overlapping progress bars, progress jumping backward, incorrect counts.

**Prevention:**
- Use a single `tqdm` progress bar in the MAIN process only.
- Update it via `imap_unordered` callback: `for result in tqdm(pool.imap_unordered(...), total=n):`
- Do NOT call `tqdm` inside worker functions.
- If workers need to log, use `multiprocessing.Queue` to send log messages back to main process.

**Phase:** Phase 1 (progress display integration).

---

### S3: File ordering in combined Markdown output is non-deterministic with `imap_unordered`

**What goes wrong:** `imap_unordered` (the correct choice for memory efficiency and tqdm integration) returns results in completion order, not input order. If the combined output file is written as results arrive, filenames appear in arbitrary order. Users expect alphabetical or natural sort order to grep specific images.

**Warning sign:** Output file has images in random order that changes between runs.

**Prevention:**
- Collect `(index, text)` tuples where `index` is the position in the pre-sorted input list.
- Sort by index before writing to the output file, OR
- Pre-assign indices and write results into a pre-allocated list, then flush sorted at the end.
- Sort input paths with `natsort` (natural sort) before processing: `natsort.natsorted(glob_results)` handles `img1, img2, img10` correctly (not lexicographic `img1, img10, img2`).

**Phase:** Phase 1 (output writing logic).

---

### S4: Output file encoding issues for non-ASCII text even in "English only" mode

**What goes wrong:** Even when EasyOCR is configured for English only, source images may contain symbols, ligatures, or Unicode punctuation (curly quotes, em dashes, accented characters in proper nouns). Writing to a file without explicit `encoding="utf-8"` uses the platform default, which is `cp1252` on Windows and may be `ASCII` in some CI environments. This causes `UnicodeEncodeError` on the first non-ASCII character.

**Warning sign:** `UnicodeEncodeError: 'ascii' codec can't encode character '’'` or garbled output on Windows.

**Prevention:**
```python
open(output_path, "w", encoding="utf-8")
```
Always explicit. No exceptions. Also add a BOM header comment or note in README that the output file is UTF-8.

**Phase:** Phase 1 (file I/O).

---

### S5: Corrupted or truncated images cause Docling to hang rather than raise

**What goes wrong:** Some image decoders (PIL/Pillow, used internally by Docling) will attempt to decode a partially written or corrupted JPEG by padding missing data, which can trigger extremely slow processing or an infinite loop in the DCT decoder. `raises_on_error=False` in `convert_all` only catches Python-level exceptions, not cases where the decoder hangs.

**Warning sign:** Batch stalls at a specific image with no progress for minutes. Worker processes show high CPU but no output.

**Prevention:**
- Pre-validate images before submitting to the converter: use `PIL.Image.open(path).verify()` in a quick sequential pass at startup. Remove or skip files that fail verification.
- Set a per-image timeout using `multiprocessing`'s worker timeout or a `signal.alarm` guard inside the worker.
- Log the specific image path when a worker exceeds N seconds.

**Phase:** Phase 1 (input discovery / pre-validation step).

---

### S6: Very large images cause OOM in individual workers

**What goes wrong:** A single 50 MP scan (e.g., 8000×6000 px) loaded by Pillow takes ~180 MB in memory uncompressed (RGB). Docling's layout pipeline may scale or pad it further. With 4 parallel workers each hitting a large image simultaneously, 4 × 180 MB = 720 MB of image data alone, plus model weights (~200–500 MB per worker).

**Warning sign:** RSS memory of worker processes spikes to several GB. OOM kill or swap exhaustion.

**Prevention:**
- Before queuing an image, check its dimensions with `PIL.Image.open(path)` (reads headers only without decoding). If width × height > threshold (e.g., 25 MP), either skip with a warning or resize before passing to Docling.
- Alternatively, reduce `Pool` worker count on machines with limited RAM.

**Phase:** Phase 1 (pre-validation / worker count tuning).

---

### S7: Worker count set to CPU count ignores RAM constraint

**What goes wrong:** `os.cpu_count()` returns logical CPU count, commonly 8–16 on modern laptops. Each Docling+EasyOCR worker needs ~800 MB–1.5 GB RAM (model weights + image buffers). On a 16 GB machine, 8 workers × 1.2 GB = 9.6 GB — feasible but tight. 16 workers will OOM.

**Warning sign:** Batch starts fast, then slows dramatically as swap kicks in, then dies.

**Prevention:**
- Default worker count to `min(os.cpu_count(), 4)` or expose `--workers N` CLI flag.
- Document the RAM-per-worker cost in the README.
- On machines with GPUs, CPU worker count matters less — consider `--workers 2` default for GPU paths.

**Phase:** Phase 1 (CLI argument defaults).

---

## Minor (annoyances to avoid)

### M1: UV `uv run` does not activate the venv for subprocess calls inside the script

**What goes wrong:** Code that spawns subprocesses (e.g., `subprocess.run(["python", ...])`) will use the system Python, not the UV-managed venv Python. This is rarely a problem for a self-contained CLI, but if any worker-spawning code uses `sys.executable` to re-invoke Python, it may accidentally invoke the wrong interpreter.

**Prevention:** Always use `sys.executable` (not `"python"`) for any subprocess that needs the same interpreter. Or use UV's `--script` feature for entry points.

**Phase:** Phase 1 (if any subprocess usage is needed).

---

### M2: `convert_all` generator exhausted silently if iterated twice

**What goes wrong:** `DocumentConverter.convert_all(paths)` returns a generator. If the calling code passes it to `list()` and also iterates it separately (e.g., for counting), the second iteration is empty. This causes silent 0-result output.

**Warning sign:** Output file exists but contains no content. No error raised.

**Prevention:** Materialize the generator exactly once: `results = list(converter.convert_all(paths))`. Or use the generator with `imap_unordered` and never re-iterate.

**Phase:** Phase 1 (result consumption).

---

### M3: Docling logs verbose DEBUG output to stderr by default

**What goes wrong:** Docling uses Python's `logging` module and may not configure a default log level, inheriting the root logger's level. This floods stderr with model inference timing, layout analysis steps, and tensor shape logs — making tqdm progress bars unreadable.

**Warning sign:** Terminal filled with lines like `DEBUG:docling.pipeline:...` during processing.

**Prevention:**
```python
import logging
logging.getLogger("docling").setLevel(logging.WARNING)
logging.getLogger("easyocr").setLevel(logging.WARNING)
```
Set this before constructing any converter. Add a `--verbose` CLI flag to re-enable DEBUG for debugging sessions.

**Phase:** Phase 1 (logging setup at entry point).

---

### M4: Natural sort not applied to glob results leads to misordered output

**What goes wrong:** `glob.glob("*.jpg")` and `Path.glob("*")` return files in filesystem order, which is arbitrary on most filesystems (not alphabetical). Even `sorted()` produces lexicographic order: `img1.jpg, img10.jpg, img11.jpg, img2.jpg`. Users almost always expect `img1, img2, ..., img10` (natural numeric sort).

**Prevention:**
```python
from natsort import natsorted
paths = natsorted(input_dir.glob("*"))
```
Add `natsort` as a dependency. It's lightweight and handles mixed numeric/alpha filenames correctly.

**Phase:** Phase 1 (input discovery).

---

### M5: Failed images silently absent from error log if exception is swallowed in worker

**What goes wrong:** Worker functions that catch broad `Exception` and return `None` without logging the path mean that failed images are dropped with no trace. The user cannot know which images failed or why.

**Warning sign:** Output file has fewer images than expected, error log is empty.

**Prevention:**
- Worker returns `(path, result_or_none, error_message_or_none)` tuples always.
- Main process writes every `error_message` that is not None to a `failures.log` file.
- At completion, print a summary: `Processed 287/300 — 13 failures. See failures.log.`

**Phase:** Phase 1 (worker return protocol and error log writing).

---

### M6: WEBP support requires Pillow built with libwebp; may be absent in minimal environments

**What goes wrong:** The project requirements include WEBP format. Pillow can be installed without libwebp support (common in minimal Docker images or old system Python envs). Attempting to open a WEBP file raises `OSError: image file is truncated` or `UnidentifiedImageError`, not a clean "format unsupported" error.

**Prevention:**
- In the pre-validation step, call `PIL.features.check_codec("webp")` at startup and warn if WEBP support is absent.
- UV with a modern Python will typically have a Pillow wheel with libwebp, but document the requirement.

**Phase:** Phase 1 (startup checks).

---

### M7: TIFF multi-page images: Docling processes only first frame by default

**What goes wrong:** Multi-page TIFF files (common in scanned document archives) contain multiple frames. Docling's image pipeline opens the file as a single image, which by default yields only the first frame. The remaining frames are silently ignored.

**Warning sign:** A 10-page TIFF scanned document produces only 1 page of text.

**Prevention:** For this project's stated use case (300 images, single flat folder), document the limitation in README: "Multi-page TIFF files are processed as single images; only the first frame is extracted." If multi-page support is needed later, pre-split TIFFs with Pillow before passing to Docling.

**Phase:** Phase 1 (README / known limitations).

---

### M8: Markdown header collisions if two files have identical stems

**What goes wrong:** The output format uses `# filename` as a header per image. If the input folder contains `scan.jpg` and `scan.png`, both produce `# scan` headers, making the combined file ambiguous to grep or parse programmatically.

**Prevention:** Use the full filename including extension as the header: `# scan.jpg` and `# scan.png`. This is unambiguous and preserves format information.

**Phase:** Phase 1 (output formatting).

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Entry point setup | Fork start method on macOS (C4) + fork bomb without `__main__` guard (C5) | Set `spawn`, wrap in `if __name__ == "__main__":` first |
| Worker initialization | Non-picklable converter (C1) + model download race (C2) + EasyOCR concurrency (C3) | Initializer function pattern; warmup before pool |
| Input discovery | Corrupted files hang (S5) + large images OOM (S6) + unsorted glob (M4) + WEBP codec (M6) | Pre-validate with Pillow verify() + dimension check + natsort |
| Parallel execution | Worker count OOM (S7) + macOS fork crashes (C4) | Default workers = min(cpu_count, 4); spawn start method |
| Result collection | Memory accumulation (S1) + generator exhausted (M2) | imap_unordered + write incrementally |
| Progress display | tqdm garbling (S2) | tqdm in main process only, driven by imap_unordered |
| Output writing | File ordering (S3) + encoding (S4) + header collisions (M8) | Sort by original index; utf-8 explicit; full filename in header |
| Logging | Docling DEBUG flood (M3) | Set WARNING level before any converter construction |
| Error handling | Silent failures (M5) + TIFF multi-frame (M7) | Return (path, result, error) tuples; document TIFF limitation |

## Sources

All findings are from training knowledge (HIGH confidence for Python multiprocessing/spawn/fork behavior; MEDIUM confidence for Docling-specific behavior as of mid-2025). Validate the following before implementation:

- Docling GitHub issues: https://github.com/DS4SD/docling/issues (filter: `multiprocessing`, `pickling`, `EasyOCR`, `memory`)
- Docling documentation: https://ds4sd.github.io/docling/
- EasyOCR GitHub: https://github.com/JaidedAI/EasyOCR/issues (filter: `multiprocessing`, `download`)
- Python multiprocessing docs: https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods
- UV documentation: https://docs.astral.sh/uv/
