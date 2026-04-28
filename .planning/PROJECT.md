# OCR Batch Processor

## What This Is

Python CLI tool that batch-processes ~300 images from a flat folder using Docling + EasyOCR, extracting text and writing all results into a single combined Markdown file with per-image headers. Built with UV for dependency management.

## Core Value

Given a folder of images, produce a single readable Markdown file with every image's extracted text — reliably, fast, with failures logged rather than silently dropped.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Accept input folder path as CLI argument
- [ ] Discover all image files in the folder (JPEG, PNG, TIFF, WEBP, BMP)
- [ ] Process images in parallel using multiprocessing
- [ ] Extract text via Docling + EasyOCR backend (English)
- [ ] Write all extracted text to single combined Markdown file (# filename headers)
- [ ] Skip failed images and write failures to error log
- [ ] Show progress during batch run
- [ ] Accept output file path as CLI argument (default: output.md)

### Out of Scope

- Nested/recursive folder scanning — single flat folder only
- Multi-language OCR — English only
- Per-image output files — single combined file only
- PDF processing — images only
- GUI — CLI only

## Context

- Docling v2+ supports images natively via `DocumentConverter`
- EasyOCR selected: no system deps, pip-only install, GPU-capable
- Batch API: `converter.convert_all(paths, raises_on_error=False)` handles failures gracefully
- UV manages venv + deps — `uv run` executes without manual activation
- ~300 images → parallel processing required to complete in reasonable time
- Docling Jobkit exists for distributed batch but is overkill for this scale

## Constraints

- **Runtime**: Python 3.11+ — Docling requirement
- **Package manager**: UV — project requirement
- **OCR**: EasyOCR via Docling — no system-level OCR deps
- **Scale**: ~300 images — multiprocessing, not distributed

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Docling over raw EasyOCR | Higher-level API, handles image normalization, structured output | — Pending |
| EasyOCR over Tesseract | No system install needed, pure pip | — Pending |
| Single combined .md output | User requirement — one file to review/grep | — Pending |
| Parallel processing | 300 images would take too long sequentially | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-28 after initialization*
