---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
current_phase: Phase 1 — Multiprocessing Foundation + Core Pipeline
status: unknown
last_updated: "2026-04-28T18:47:39.874Z"
---

# STATE — OCR Batch Processor

## Project Reference

**Core Value:** Given a folder of images, produce a single readable Markdown file with every image's extracted text — reliably, fast, with failures logged rather than silently dropped.

**Current Phase:** Phase 1 — Multiprocessing Foundation + Core Pipeline

**Current Focus:** Roadmap created; awaiting phase planning.

---

## Current Position

| Aspect | Status |
|--------|--------|
| Roadmap | ✓ Created (2 phases) |
| Phase Plan | ✓ Created (PLAN.md verified) |
| Implementation | ⏳ 75% (Waves 1-3 done, Wave 4 ready) |
| Testing | ⏳ In progress (unit tests running) |
| Validation | ⏳ Pending Wave 4 |

## Execution Progress

**Wave 1 ✅ COMPLETE**
- discovery.py (natural sort + validation)
- logger.py (JSONL error logging)
- All hazards mitigated: M4, S5, S6, M6

**Wave 2 ✅ COMPLETE**
- worker.py (per-worker converter init)
- processor.py (Pool + warmup + progress)
- models.py (ProcessResult dataclass)
- All critical hazards mitigated: C1, C2, C3, S1, S2, S7

**Wave 3 ✅ COMPLETE**
- writer.py (incremental UTF-8 output) — commit 03318d5
- cli.py (Typer orchestration) — commit eff730d
- __main__.py (spawn guard) — commit d5774ea
- All hazards mitigated: C4, C5, S4, M3, M8

**Wave 4 ⏳ PENDING**
- E2E integration tests
- Multiprocessing safety tests
- Success criteria validation

---

## Performance Metrics

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Batch throughput | ~300 images in <10min | — Pending | — |
| Worker count | 4 (default, tunable) | — Pending | — |
| Memory per worker | <1.5 GB | — Pending | — |
| Output file write | Incremental (crash-safe) | — Pending | — |
| Error capture | 100% with context | — Pending | — |

---

## Accumulated Context

### Critical Multiprocessing Pitfalls (from PITFALLS.md)

**Must address in Phase 1 implementation:**

- **C1:** DocumentConverter is NOT picklable — each worker must instantiate its own via initializer function
- **C2:** Model download race on first run — add warmup step before spawning pool
- **C3:** EasyOCR initialization not thread-safe — warmup pre-populates cache before workers spawn
- **C4:** Fork start method unsafe on macOS with PyTorch — explicitly use `spawn` in `if __name__ == "__main__":`
- **C5:** UV `uv run` + spawn multiprocessing requires full `if __name__ == "__main__":` guard to prevent fork bombs

**Significant pitfalls to mitigate:**

- **S1:** OOM with large batches — use `imap_unordered()` with incremental writes, not `pool.map()`
- **S2:** tqdm garbling with multiprocessing — single progress bar in main process only
- **S3:** Non-deterministic output order with `imap_unordered` — document or sort by original index
- **S4:** UTF-8 encoding issues — always explicit `encoding="utf-8"` when opening output file
- **S5:** Corrupted images hang decoder — pre-validate with `PIL.Image.open().verify()` before processing
- **S6:** Large images OOM workers — check dimensions; resize or skip if >25 MP
- **S7:** Worker count ignores RAM — default to `min(cpu_count, 4)`, expose `--workers N` flag

### Build Order (Phase 1)

Per ARCHITECTURE.md:

1. `discovery.py` — pure stdlib, no OCR
2. `logger.py` — pure stdlib error logging
3. `worker.py` — Docling + EasyOCR in initializer
4. `processor.py` — multiprocessing.Pool orchestration
5. `writer.py` — incremental output file writing
6. `cli.py` + `__main__.py` — entry point and module invocation

### Component Responsibilities

- **discovery.py:** File discovery, extension filtering, natural sorting (natsort)
- **logger.py:** JSONL error log format (file, error_type, message, timestamp)
- **worker.py:** ProcessResult dataclass; _init_worker() loads DocumentConverter; process_image(path) → ProcessResult
- **processor.py:** Pool lifecycle, imap_unordered with tqdm progress, chunksize=1
- **writer.py:** Incremental output file append with UTF-8, full filename headers, `---` separators
- **cli.py:** Argument parsing (Typer), validation, orchestration, summary printing
- **__main__.py:** Entry point guard and main() invocation

---

## Decisions Logged

| Decision | Phase | Rationale | Status |
|----------|-------|-----------|--------|
| Use multiprocessing.Pool + spawn | Phase 1 | Process-level parallelism; fork unsafe on macOS | — Pending validation |
| Load DocumentConverter per-worker via initializer | Phase 1 | Not picklable; amortize startup across images | — Pending validation |
| Use imap_unordered + tqdm in main | Phase 1 | Memory efficiency; avoid result buffering | — Pending validation |
| Accept completion-order output (no reordering) | Phase 1 | Simpler; adequate for MVP grep use case | — Pending validation |
| Default 4 workers, override with --workers N | Phase 1 | Fits 16GB RAM; expose tuning flag | — Pending validation |
| Warmup before Pool spawn | Phase 1 | Pre-populate model caches, avoid download race | — Pending validation |
| JSONL error log (not CSV) | Phase 1 | Structured; no quoting complexity | — Pending validation |
| Full filename (not stem) in Markdown headers | Phase 1 | No ambiguity; preserves format info | — Pending validation |

---

## Todos

- [ ] Verify Docling v2 + EasyOCR API signatures against installed versions
- [ ] Confirm natsort library availability and API
- [ ] Test spawn start method behavior on target OS (macOS if developing there)
- [ ] Design warmup strategy (dummy conversion vs. build-in-main pattern)
- [ ] Validate tqdm + imap_unordered progress display behavior
- [ ] Design ProcessResult dataclass schema
- [ ] Design errors.jsonl schema and validation

---

## Blockers

None identified at roadmap stage. Phase 1 planning will validate API assumptions.

---

## Session Continuity

**Last Update:** 2026-04-28 (Wave 3 complete)  
**Next Step:** `/gsd-execute-phase 1 --wave 4` to run integration tests and validate all 8 success criteria

---

*State captured at roadmap initialization. Updates via phase transitions and milestone completion.*
