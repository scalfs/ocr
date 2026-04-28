# ROADMAP — OCR Batch Processor

## Phases

- [ ] **Phase 1: Multiprocessing Foundation + Core Pipeline** - Deliver all v1 features with critical multiprocessing discipline
- [ ] **Phase 2: Polish & Observability** - Dry-run mode, verbose/quiet, worker tuning, performance reporting

---

## Phase Details

### Phase 1: Multiprocessing Foundation + Core Pipeline

**Goal**: Batch process ~300 images reliably with proper multiprocessing discipline, producing a single combined Markdown file with all extracted text and complete error logging.

**Depends on**: None (first phase)

**Requirements**: REQ-01, REQ-02, REQ-03, REQ-04, REQ-05, REQ-06, REQ-07, REQ-08, REQ-09, REQ-10, REQ-11, REQ-12, REQ-13, REQ-14, REQ-15, REQ-16, REQ-17, REQ-18, REQ-19, REQ-20, REQ-21, REQ-22, REQ-23, REQ-24, REQ-25

**Success Criteria** (what must be TRUE):
1. User can invoke CLI with input folder path and receive a combined Markdown output file with all extracted text organized by filename headers
2. Processing completes successfully across 300 images in parallel with N worker processes (default 4)
3. Failed images are skipped gracefully and logged to errors.jsonl with type, message, and timestamp
4. Progress bar displays live during batch processing without visual corruption or duplicated output
5. Worker processes initialize safely on macOS/Windows using spawn start method with non-picklable DocumentConverter instances loaded per-worker
6. Output file is written incrementally per image (crash-safe) with UTF-8 encoding and full filenames in headers
7. Final summary shows total processed, skipped, failed counts, and elapsed time; exit code reflects success (0) or failures (1)
8. All image files (JPEG, PNG, TIFF, WEBP, BMP) are discovered, naturally sorted, validated before processing, and failures are captured with full error context

**Plans**: TBD

---

### Phase 2: Polish & Observability

**Goal**: Add observability features, operational flags, and performance tuning for production-ready batch processing.

**Depends on**: Phase 1

**Requirements**: None (v1 complete; v2 features deferred)

**Success Criteria** (what must be TRUE):
1. User can run with `--dry-run` flag to validate input folder, list files to process, and estimate time without executing OCR
2. User can control console verbosity with `--verbose` (debug-level logging) and `--quiet` (errors only) flags
3. User can tune worker count with `--workers N` CLI flag to match available RAM and CPU
4. Final summary includes throughput metric (images/minute) and performance insights
5. OCR quality warnings appear for empty or suspiciously short extracted text per image

**Plans**: TBD

---

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Multiprocessing Foundation + Core Pipeline | 0/6 | Not started | — |
| 2. Polish & Observability | 0/5 | Not started | — |

---

## Coverage Summary

**Total v1 Requirements:** 25
**Phase 1 Requirements:** 25
**Phase 2 Requirements:** 0 (v2 deferred)

**Coverage:** 25/25 (100%) ✓

All v1 requirements mapped to Phase 1. Phase 2 addresses v2 polish features (deferred).
