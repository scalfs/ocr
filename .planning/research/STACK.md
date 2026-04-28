# Stack — OCR Batch Processor

**Project:** OCR Batch Processor
**Researched:** 2026-04-28
**Mode:** Ecosystem (greenfield, prescribed stack)

---

## Core Stack

| Library | Version | Role | Why |
|---------|---------|------|-----|
| Python | 3.11+ | Runtime | Docling hard requirement; 3.11 gives significant speed gains over 3.10 via interpreter optimizations |
| docling | >=2.0 (pin after `uv add`) | Document conversion + OCR orchestration | Provides `DocumentConverter` + `EasyOcrOptions`; handles image normalization, structured text extraction, and graceful failure via `convert_all(raises_on_error=False)` |
| easyocr | >=1.7 (pulled as docling dep) | OCR backend | No system-level dependencies (no Tesseract install); pure pip; GPU-capable via PyTorch; English-only is the simplest config |
| typer | >=0.12 | CLI framework | Type-hint-driven (vs Click's decorator API); minimal boilerplate; auto-generates `--help`; preferred for greenfield Python tools in 2024-2025 |
| rich | >=13.0 | Progress display + console output | Powers Typer's output; `rich.progress.Progress` gives per-item progress bars for 300-image batch; also used by Docling internally |
| Python stdlib `concurrent.futures` | (stdlib, 3.11) | Parallel batch dispatch | `ProcessPoolExecutor` is the right primitive for CPU-bound parallel work at this scale — no extra dep, battle-tested, correct for 300 images |

---

## UV Setup

```bash
# Initialize project (creates pyproject.toml + .venv)
uv init ocr-batch

# Add runtime dependencies
uv add docling typer rich

# EasyOCR is pulled as a docling dependency, but pin it explicitly
# if you need a specific version:
# uv add "easyocr>=1.7"

# Run the tool without activating venv manually
uv run python -m ocr_batch /path/to/images
# or if entry point is defined:
uv run ocr-batch /path/to/images
```

### pyproject.toml structure

```toml
[project]
name = "ocr-batch"
version = "0.1.0"
description = "Batch image OCR using Docling + EasyOCR"
requires-python = ">=3.11"
dependencies = [
    "docling>=2.0",
    "typer>=0.12",
    "rich>=13.0",
]

[project.scripts]
ocr-batch = "ocr_batch.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = []
```

---

## Key Configuration

### EasyOcrOptions setup

```python
from docling.datamodel.pipeline_options import EasyOcrOptions
from docling.document_converter import DocumentConverter

ocr_options = EasyOcrOptions(
    lang=["en"],          # English only — matches project scope
    use_gpu=False,        # Set True if CUDA is available; auto-detection not reliable across envs
)

converter = DocumentConverter(
    pipeline_options={"ocr_options": ocr_options}
)
```

**GPU flag:** `use_gpu=True` activates EasyOCR's PyTorch CUDA path. For a 300-image batch on a machine with a GPU this roughly halves processing time. Default to `False` for portability; expose as a `--gpu/--no-gpu` CLI flag.

### InputFormat for images

```python
from docling.datamodel.base_models import InputFormat

# Supported image InputFormat values (Docling v2):
# InputFormat.IMAGE covers: JPEG, JPG, PNG, TIFF, BMP, WEBP
# There is a single enum value for all raster images — not per-extension

from pathlib import Path
from docling.document_converter import DocumentConverter, ImageFormatOption

# Batch conversion — the correct pattern
paths = list(image_dir.glob("*.jpg")) + list(image_dir.glob("*.png"))  # etc.

results = converter.convert_all(
    paths,
    raises_on_error=False,   # skip failures, don't abort the batch
)

for result in results:
    if result.status.success:
        md_text = result.document.export_to_markdown()
    else:
        # log result.input.file and result.status.error_message
        pass
```

### Parallel processing — ProcessPoolExecutor

Docling's `convert_all()` is a synchronous generator that processes one document at a time internally. For a 300-image batch, the correct approach is to shard the file list across workers using `ProcessPoolExecutor`, with each worker running its own `DocumentConverter` instance (Docling models are not safely shareable across processes).

```python
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

def process_chunk(image_paths: list[Path]) -> list[tuple[str, str | None, str | None]]:
    """Run in a subprocess. Returns list of (filename, markdown_text, error)."""
    from docling.document_converter import DocumentConverter
    from docling.datamodel.pipeline_options import EasyOcrOptions

    ocr_options = EasyOcrOptions(lang=["en"], use_gpu=False)
    converter = DocumentConverter(
        pipeline_options={"ocr_options": ocr_options}
    )
    results = []
    for res in converter.convert_all(image_paths, raises_on_error=False):
        if res.status.success:
            results.append((res.input.file.name, res.document.export_to_markdown(), None))
        else:
            results.append((res.input.file.name, None, str(res.status.error_message)))
    return results

def run_parallel(paths: list[Path], workers: int = 4) -> ...:
    chunk_size = max(1, len(paths) // workers)
    chunks = [paths[i:i+chunk_size] for i in range(0, len(paths), chunk_size)]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_chunk, chunk): chunk for chunk in chunks}
        for future in as_completed(futures):
            yield from future.result()
```

**Why ProcessPoolExecutor over Pool:** `ProcessPoolExecutor` integrates cleanly with `concurrent.futures.as_completed` for streaming results to the progress bar as chunks finish. `multiprocessing.Pool` requires `starmap` or `imap` and is harder to pair with Rich progress.

**Why not docling-jobkit:** Designed for distributed cluster workloads. Adds Ray/Dask overhead. 300 images on a single machine is well within `ProcessPoolExecutor` territory.

---

## What NOT to Use

| Rejected | Alternative Used | Reason |
|----------|-----------------|--------|
| `argparse` | Typer | Argparse requires manual type coercion, no auto-help formatting, significantly more boilerplate for same result |
| `click` (directly) | Typer | Typer wraps Click; direct Click use requires decorators and manual type handling that Typer eliminates; Typer is Click for modern Python |
| `tqdm` | Rich | Rich provides progress bars with the same API surface, but also handles all console output (errors, logs, panels) in one library; mixing tqdm + rich causes display conflicts |
| `docling-jobkit` | `ProcessPoolExecutor` (stdlib) | Jobkit is for distributed multi-machine workloads with Ray. Zero benefit at 300 images; adds heavy deps |
| `multiprocessing.Pool` | `concurrent.futures.ProcessPoolExecutor` | Pool's `imap`/`starmap` harder to compose with `as_completed` streaming; ProcessPoolExecutor is the modern stdlib approach (PEP 3148) |
| Raw EasyOCR (no Docling) | Docling + EasyOcrOptions | Would require manual image loading, text extraction, and formatting; Docling handles normalization, layout, and `export_to_markdown()` |
| Tesseract / pytesseract | EasyOCR | Requires system-level Tesseract install; breaks `pip`-only portability requirement |
| `asyncio` | `ProcessPoolExecutor` | OCR is CPU-bound (model inference), not I/O-bound; async provides no benefit and adds complexity |

---

## Confidence Notes

| Claim | Confidence | Note |
|-------|-----------|------|
| Docling v2 `DocumentConverter` + `convert_all()` API | HIGH | Stable API present in all Docling v2.x releases through training cutoff (Aug 2025) |
| `EasyOcrOptions(lang=[...], use_gpu=...)` constructor signature | HIGH | Documented in docling source and examples consistently |
| `InputFormat.IMAGE` covers all raster formats | MEDIUM | The enum structure is correct; verify exact enum values with `python -c "from docling.datamodel.base_models import InputFormat; print(list(InputFormat))"` after install |
| `result.status.success` and `result.document.export_to_markdown()` | HIGH | Core Docling result API — stable across v2 |
| `raises_on_error=False` parameter name | HIGH | Documented as the graceful-failure parameter for `convert_all` |
| Exact current docling PyPI version number | LOW — VERIFY | Run `uv add docling` and check `uv.lock` for pinned version; do not hardcode a specific semver without checking |
| Exact current easyocr PyPI version | LOW — VERIFY | EasyOCR version pulled as transitive dep; check `uv.lock` after install |
| Typer >=0.12 | HIGH | Typer 0.12 (2024) added significant improvements; 0.9+ is stable for this use case |
| Rich >=13.0 | HIGH | Rich 13.x is the current stable series through training cutoff |
| GPU acceleration via `use_gpu=True` | MEDIUM | EasyOCR's GPU path requires matching PyTorch + CUDA; works correctly when environment matches, but don't make it the default |

**Recommended verification step after `uv add docling`:**

```bash
uv run python -c "
import docling, easyocr, typer, rich
print('docling:', docling.__version__)
print('easyocr:', easyocr.__version__)
print('typer:', typer.__version__)
print('rich:', rich.__version__)
from docling.datamodel.base_models import InputFormat
print('InputFormat values:', list(InputFormat))
"
```

This confirms actual installed versions and prints all valid `InputFormat` enum values for image format filtering logic.
