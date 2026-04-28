"""Module entry point for OCR batch processor.

Enables invocation via: python -m ocr_batch

C4/C5 MITIGATION:
- Sets multiprocessing start method to 'spawn' BEFORE any Pool creation
- All Pool construction happens inside if __name__ == "__main__": guard
  to prevent fork bombs when invoked via uv run or similar

This is the ONLY place that should set_start_method to avoid conflicts.
"""

import multiprocessing as mp

from .cli import app


if __name__ == "__main__":
    # C4 MITIGATION: Explicitly set spawn start method for PyTorch/Docling safety on macOS
    # Must be done before any Pool construction or model initialization
    mp.set_start_method("spawn", force=True)

    # C5 MITIGATION: __main__ guard prevents fork bombs when module is invoked
    app()
