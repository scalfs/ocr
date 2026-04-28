"""CLI entry point for OCR batch processor.

Handles argument parsing (Typer), input validation, logging setup, and orchestration.

Key responsibilities:
- Parse CLI arguments: input_dir, output, workers, gpu, log, verbose
- Validate input folder exists and is readable
- Validate output path is writable
- Setup logging: suppress Docling/EasyOCR DEBUG output (M3 mitigation)
- Orchestrate: discovery → validate → logger + writer + processor loop
- Print summary: processed/skipped/failed counts, elapsed time
- Exit with code 0 (no failures) or 1 (failures occurred)

Hazards addressed:
- M3: Docling DEBUG flood — set logging levels before any converter creation
"""

import logging
import sys
import time
from pathlib import Path

import typer
from rich.console import Console

from . import discovery, processor, worker
from .logger import ErrorLogger
from .writer import OutputWriter

# Setup console for output
console = Console()

# Create Typer app
app = typer.Typer(
    help="Batch OCR processor: Extract text from images in parallel",
    no_args_is_help=True,
)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging levels globally.

    M3 MITIGATION: Suppress DEBUG output from Docling and EasyOCR before
    any converter instantiation to prevent log flooding.

    Args:
        verbose: If True, enable DEBUG logging; otherwise, suppress to WARNING.
    """
    # Determine log level
    log_level = logging.DEBUG if verbose else logging.WARNING

    # Suppress library debug output
    logging.getLogger("docling").setLevel(log_level)
    logging.getLogger("easyocr").setLevel(log_level)
    logging.getLogger("PIL").setLevel(log_level)

    # Configure root logger (ocr_batch modules will use INFO/DEBUG)
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(name)s: %(message)s",
    )


@app.command()
def main(
    input_dir: str = typer.Argument(
        ...,
        help="Input folder containing images",
    ),
    output: str = typer.Option(
        "output.md",
        "--output",
        "-o",
        help="Output Markdown file path",
    ),
    workers: int = typer.Option(
        None,
        "--workers",
        "-w",
        help="Number of worker processes (default: 4, capped at CPU count)",
    ),
    gpu: bool = typer.Option(
        False,
        "--gpu",
        help="Use GPU for OCR (default: False)",
    ),
    log: str = typer.Option(
        "errors.jsonl",
        "--log",
        "-l",
        help="Error log file (JSONL format)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging",
    ),
) -> int:
    """Batch process images to extract text via OCR.

    Discovers all supported image formats in INPUT_DIR, processes them in parallel,
    and writes combined Markdown to OUTPUT with errors logged to JSONL.

    Returns:
        0 if all images processed successfully (or no failures)
        1 if any images failed to process
    """
    # Setup logging early (M3 mitigation: before any converter instantiation)
    setup_logging(verbose=verbose)

    start_time = time.perf_counter()

    try:
        # Validate input directory
        input_path = Path(input_dir)
        if not input_path.exists():
            console.print(f"[red]Error: Input directory does not exist: {input_path}[/red]")
            return 1
        if not input_path.is_dir():
            console.print(f"[red]Error: Input path is not a directory: {input_path}[/red]")
            return 1
        if not input_path.stat().st_mode & 0o400:
            console.print(f"[red]Error: Input directory is not readable: {input_path}[/red]")
            return 1

        # Validate output directory is writable
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Test write permission
            test_file = output_path.parent / ".ocr_test_write"
            test_file.touch()
            test_file.unlink()
        except (IOError, OSError) as e:
            console.print(f"[red]Error: Cannot write to output directory: {e}[/red]")
            return 1

        # Discover and validate images
        console.print(f"[cyan]Discovering images in {input_path}...[/cyan]")
        paths = discovery.discover_images(input_path)
        console.print(f"[green]Found {len(paths)} images[/green]")

        if not paths:
            console.print("[yellow]No images found to process[/yellow]")
            return 0

        # Validate images (pre-check for corruption and size)
        console.print("[cyan]Validating images...[/cyan]")
        valid_paths, skipped_paths = discovery.validate_images(paths)
        console.print(f"[green]Validated {len(valid_paths)} images[/green]")
        if skipped_paths:
            console.print(f"[yellow]Skipped {len(skipped_paths)} corrupted/oversized files[/yellow]")

        # Initialize error logger and output writer
        error_logger = ErrorLogger(Path(log), timestamp_utc=True)
        output_writer = OutputWriter(output_path, original_paths=valid_paths)

        # Process all valid images
        console.print(f"[cyan]Processing images with {workers or 4} workers...[/cyan]")
        error_count = 0

        for result in processor.process_all(
            valid_paths,
            worker_count=workers,
            use_gpu=gpu,
            verbose=verbose,
        ):
            if result.error:
                # Log error
                error_logger.log_error(
                    filename=result.path.name,
                    error_type=result.error_type or "UnknownError",
                    message=result.error,
                    duration_s=result.duration_s,
                )
                error_count += 1
            else:
                # Write successful result to output
                output_writer.write_result(result)

        # Calculate elapsed time
        elapsed_time = time.perf_counter() - start_time
        elapsed_str = format_elapsed_time(elapsed_time)

        # Print summary
        writer_counts = output_writer.get_counts()
        processed = writer_counts["processed"]
        failed = error_count

        console.print("\n[bold cyan]Summary[/bold cyan]")
        console.print(f"  Processed: {processed}")
        console.print(f"  Skipped:   {len(skipped_paths)}")
        console.print(f"  Failed:    {failed}")
        console.print(f"  Duration:  {elapsed_str}")
        if failed > 0:
            console.print(f"  Errors logged to: {log}")

        # Return appropriate exit code
        return 1 if failed > 0 else 0

    except Exception as e:
        console.print(f"[red]Error: {type(e).__name__}: {e}[/red]")
        if verbose:
            console.print_exception()
        return 1


def format_elapsed_time(seconds: float) -> str:
    """Format elapsed time as HH:MM:SS.

    Args:
        seconds: Elapsed time in seconds

    Returns:
        Formatted string (e.g., "00:12:34")
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


if __name__ == "__main__":
    sys.exit(app())
