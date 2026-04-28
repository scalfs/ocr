# Features — OCR Batch Processor

**Domain:** Batch image OCR CLI tool
**Researched:** 2026-04-28
**Confidence:** MEDIUM — training knowledge of OCRmyPDF, Tesseract CLI, ImageMagick, ffmpeg batch patterns, and Python CLI conventions. No live web access during this session.

---

## Table Stakes (must have or it's broken)

These are the features users expect from any batch CLI tool processing hundreds of files. Missing any one of them makes the tool feel broken or untrustworthy.

| Feature | Why It's Required | Notes |
|---------|-------------------|-------|
| **Input folder path argument** | The entire point — without it there's no tool | `--input` or positional arg; both are conventional |
| **Output file path argument** | Users need to control where results land | Default `output.md` is fine; explicit arg required |
| **Progress indicator** | 300 images take minutes; without feedback the tool looks frozen | tqdm bar showing N/total is the Python-ecosystem standard |
| **File type filtering** | Folder may contain non-image files (`.DS_Store`, `.txt`, `.json`) | Must include JPEG, PNG, TIFF, WEBP, BMP; silently skip others |
| **Error log** | Failures MUST be visible; silent drops destroy trust | Write to a `.errors.log` or surface at end of run |
| **Sorted/deterministic file ordering** | Output must be reproducible across runs | Sort by filename (lexicographic) by default; numbered filenames sort correctly |
| **Per-image section headers** | The stated output format — `# filename` headers in Markdown | Without this the output is a wall of text with no navigation |
| **Non-zero exit code on partial failure** | Scripts and CI pipelines depend on exit codes | Exit 0 only if zero errors; exit 1 (or 2) if any images failed |
| **Graceful handling of empty/unreadable images** | Some images will be corrupt or blank | Skip + log, never crash the whole run |
| **Summary on completion** | "Processed 297/300, 3 failed" is the minimum the user needs | Print to stderr or stdout at the very end |

---

## Differentiators (make it notably better)

These features are not expected in a v1 batch OCR tool but provide real, concrete value for the actual use case.

| Feature | Value Proposition | Complexity |
|---------|-------------------|------------|
| **`--dry-run` mode** | Preview which files would be processed without running OCR — useful before committing a long run | Low — just discover + filter + print, skip OCR |
| **`--verbose` / `--quiet` flags** | `--quiet` suppresses progress bar for scripted/piped use; `--verbose` prints per-file status | Low — log-level flag wired to tqdm and logging |
| **Resume / checkpoint support** | If a 300-image run dies at image 250, restarting from zero wastes ~45 minutes | Medium-High — requires a state file tracking completed filenames |
| **Error summary at end** | Print the list of failed files with reasons at the very end of output, not just in the log file | Low — collect errors in memory, print at exit |
| **`--workers N` flag** | Let the user tune parallelism to their machine (CPU count vs. I/O vs. memory limits) | Low — already wiring multiprocessing; just expose the pool size |
| **File glob / extension filter flag** | `--include "*.jpg,*.png"` lets users process a subset without editing the folder | Low — fnmatch filter on discovery |
| **Elapsed time + throughput in summary** | "Processed 300 images in 4m 23s (1.14 img/s)" helps users benchmark and plan future runs | Low — track wall time, divide |
| **`--output-format` flag for separator style** | Some users want `---` HR separators between sections instead of `#` headers; others want both | Low — template the section separator |
| **Per-file timing in verbose mode** | Surfaces which images are slow (large files, complex layouts) — good for debugging | Low — record per-image wall time |
| **Warn on near-empty OCR output** | If OCR returns fewer than N characters for an image, warn that it may be blank or poorly scanned | Low — character count threshold check |

---

## Anti-Features (deliberately NOT building)

These are features that would expand scope without serving the core use case. Building them in v1 fragments attention and creates maintenance burden.

| Anti-Feature | Why to Avoid | What to Do Instead |
|--------------|--------------|-------------------|
| **Web UI / dashboard** | The user is a developer running a script; a browser UI is overhead | CLI is the right interface |
| **Database storage of results** | Single Markdown file is the stated requirement; a DB adds query complexity for zero gain | Markdown is grep-able and human-readable |
| **Cloud upload / S3 sync** | Out of scope; adds auth complexity, network dependency, and cost concerns | User handles storage after the fact |
| **Per-image output files** | Explicitly out of scope per PROJECT.md; forces the user to manage 300 files | Single combined file is the requirement |
| **PDF processing** | Explicitly out of scope per PROJECT.md; different codepath, different deps | Separate tool if ever needed |
| **Recursive folder scanning** | Explicitly out of scope per PROJECT.md; adds complexity and changes the mental model | Flat folder only |
| **Multi-language OCR** | Explicitly out of scope per PROJECT.md; increases model size and config surface | English-only keeps deps lean |
| **GUI installer / packaged .app** | Over-engineered for a developer-facing tool | `uv run` or `pip install` is enough |
| **Watch mode / inotify** | Real-time processing on folder changes is a different product (pipeline daemon) | Out of scope for batch tool |
| **Confidence score output** | EasyOCR provides per-character/word confidence but surfacing it in Markdown adds noise for no v1 benefit | Could be a future `--include-confidence` flag |
| **Image preprocessing (deskew, denoise)** | Docling handles normalization; manual preprocessing is a separate concern | Let Docling do its job |
| **Interactive prompt for each failure** | Breaks parallelism, destroys batch automation | Skip + log is the right failure model |

---

## Feature Complexity Notes

### Harder than they look

**Resume / checkpoint support** (MEDIUM-HIGH complexity)
The naive approach is to write completed filenames to a `.checkpoint` file after each success. The hard part: what if the output Markdown file is partially written? On resume you must either re-append (risking duplicate sections) or regenerate the whole file from checkpointed text. Options: (a) write individual `.txt` files per image as intermediate artifacts and merge at the end, or (b) store extracted text in the checkpoint file itself. Either way, it's not a simple flag — it changes the output architecture. Recommend deferring to v2.

**Deterministic file ordering** (LOW-MEDIUM complexity, easy to get wrong)
Sorting by filename seems trivial until you hit `img1.jpg, img10.jpg, img2.jpg` (lexicographic ordering). If filenames have numeric components, users almost certainly expect `img1, img2, img10` — natural sort order. Python's default `sorted()` gives lexicographic; `natsort` gives natural sort. Decision: use `natsort` (pure-Python, no system deps) for predictable output with numbered image sets. This matters a lot for the combined Markdown file to be readable in the right order.

**Progress indicator with parallel workers** (LOW-MEDIUM complexity)
tqdm works fine for sequential iteration. With multiprocessing, you need `tqdm` + `multiprocessing.Pool` integration or a `Manager().Queue()` to update the bar from worker processes. This is well-trodden (tqdm's `process_map` helper handles it), but it's not a one-liner.

**Exit code semantics** (LOW complexity, easy to skip accidentally)
Python scripts exit 0 by default. Remembering to `sys.exit(1)` when any errors occurred is easy to forget and painful to debug in scripts. Make it explicit: track error count, exit non-zero if > 0.

**File type detection** (LOW complexity, subtle gotcha)
Extension-based filtering is fast but brittle — `file.JPG` vs `file.jpg` on case-sensitive filesystems, files with no extension, or files with wrong extensions (e.g., a PNG named `.jpeg`). Use extension matching with `.lower()` and optionally validate with `imghdr` / `filetype` library for robustness. For v1, lowercase extension matching is fine and fast.

### Simpler than they look

**`--dry-run`** — file discovery + filtering + print. No OCR called. Trivial to add once discovery logic is extracted into its own function.

**`--workers N`** — one `argparse` argument feeds directly into `multiprocessing.Pool(processes=N)`. Nearly free once the parallel architecture exists.

**Error summary at end** — collect `(filename, error_message)` tuples in a shared list, print at exit. Five lines of code.

**Elapsed time + throughput** — `time.time()` before and after, division. One line.

---

## Output Format Considerations

OCR tools commonly produce: plain text (`.txt`), structured JSON (bounding boxes + text), hOCR (HTML-based), PDF with text layer, ALTO XML (archival standard), and Markdown.

For this project, Markdown is the right choice because:
- Human-readable in any editor
- `grep`-able for finding content across images
- `# filename` headers enable per-section navigation in editors that support outlines
- Compatible with static site generators if the user wants to publish later

The `---` horizontal rule as an alternative separator (instead of `#` headers) is worth offering as a flag since some Markdown renderers handle HR-separated sections better for long documents. Not blocking for v1.

---

## UX Patterns from Comparable Batch CLIs

These norms come from established tools (ffmpeg batch, ImageMagick mogrify, exiftool batch, OCRmyPDF):

- **Progress bar to stderr, content to stdout (or file)** — separates toolable output from human feedback; allows `> output.md` piping without progress noise
- **Errors to stderr + error log file** — users want both: visible on terminal AND persistent for later review
- **Summary line at the very end** — last thing printed; first thing checked when the run finishes
- **`--quiet` suppresses progress, not errors** — errors are always visible; only decorative output is suppressed
- **Consistent exit codes** — 0 = full success, 1 = partial success (some files failed), 2 = total failure (no files processed)
- **Print the resolved absolute paths** — when the user passes `./images`, echo back the absolute path so there's no ambiguity about what was processed
