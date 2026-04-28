# Requirements — OCR Batch Processor v1

## v1 Requirements (Core)

### Input & File Discovery

- [ ] **REQ-01**: User can pass input folder path as positional argument
- [ ] **REQ-02**: Tool discovers all image files (JPEG, PNG, TIFF, WEBP, BMP) in folder
- [ ] **REQ-03**: User can filter by file extension via glob pattern (e.g., `*.jpg`)
- [ ] **REQ-04**: Tool naturally sorts filenames (img1, img2, img10 — not img1, img10, img2)
- [ ] **REQ-05**: Tool validates input folder exists before processing

### OCR Processing

- [ ] **REQ-06**: Tool extracts text from each image using Docling + EasyOCR (English)
- [ ] **REQ-07**: Processing uses parallel workers (multiprocessing, default 4 workers)
- [ ] **REQ-08**: Tool shows live progress bar during batch processing
- [ ] **REQ-09**: Tool skips corrupted/unreadable images rather than halting
- [ ] **REQ-10**: Tool continues processing after any single image failure

### Output

- [ ] **REQ-11**: User can pass output filename as `--output` argument (default: output.md)
- [ ] **REQ-12**: Output file is Markdown with `# filename` header per image
- [ ] **REQ-13**: Output file contains raw extracted text under each header
- [ ] **REQ-14**: Output file uses UTF-8 encoding
- [ ] **REQ-15**: Output file is written incrementally (crash-safe, no buffering)

### Error Handling & Logging

- [ ] **REQ-16**: Tool logs all failed images to `errors.jsonl` (one JSON object per line)
- [ ] **REQ-17**: Error log includes: filename, error type, timestamp
- [ ] **REQ-18**: Tool displays error count in final summary
- [ ] **REQ-19**: Tool exits with code 0 (success) or 1 (failures occurred)
- [ ] **REQ-20**: Tool prints completion summary: files processed, skipped, failed, elapsed time

### CLI & UX

- [ ] **REQ-21**: Tool provides `--help` output (via Typer)
- [ ] **REQ-22**: Tool accepts input path as required positional argument
- [ ] **REQ-23**: Tool accepts `--output` as optional output path
- [ ] **REQ-24**: Tool accepts `--gpu/--no-gpu` flag (default: --no-gpu)
- [ ] **REQ-25**: Tool runs via `python -m ocr_batch <input_folder>`

## v2 Requirements (Polish & Observability)

- [ ] `--dry-run` mode (validate setup, show files to process, don't process)
- [ ] `--verbose` / `--quiet` modes (control console noise)
- [ ] `--workers N` flag (override default 4-worker pool size)
- [ ] Elapsed time + throughput summary (images/minute)
- [ ] Warn if OCR output is empty or suspiciously short

## Out of Scope (v1)

- **Nested/recursive folders** — Single flat folder only; use symlinks or scripts for recursive scans
- **Multi-language OCR** — English only; multi-language support deferred to v2+
- **Per-image output files** — Single combined Markdown file only; splitting handled externally
- **PDF processing** — Images only; Docling's PDF support not used in v1
- **Web UI / Database / Cloud storage** — CLI tool only; no backend infrastructure
- **Watch mode / Live monitoring** — One-shot batch processing only
- **Resume from checkpoint** — Process full batch or none; state tracking deferred to v2+

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| REQ-01 to REQ-25 | Phase 1 | — Pending |
| v2 Requirements | Phase 2 | — Out of scope v1 |

---
*Last updated: 2026-04-28 after research completion*
