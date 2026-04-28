"""Image discovery, validation, and natural sorting.

Handles:
- Discovering images by extension (case-insensitive)
- Natural sorting (img1, img2, img10 order)
- Pre-validation with PIL (corrupted images, oversized)
- WEBP codec availability check
"""

import logging
from pathlib import Path

import natsort
from PIL import Image, features

logger = logging.getLogger(__name__)

# Supported image extensions (case-insensitive)
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp", ".bmp"}


def discover_images(folder: Path, pattern: str = None) -> list[Path]:
    """Discover all image files in folder, naturally sorted.

    Args:
        folder: Path to folder containing images
        pattern: Optional glob pattern (e.g., "*.jpg"). If None, discovers all supported formats.

    Returns:
        List of Path objects, naturally sorted (img1, img2, img10 order).

    Raises:
        ValueError: If folder does not exist or is not a directory.
    """
    folder = Path(folder)
    if not folder.exists():
        raise ValueError(f"Folder does not exist: {folder}")
    if not folder.is_dir():
        raise ValueError(f"Not a directory: {folder}")

    # Discover all supported extensions
    all_paths = []
    if pattern:
        # User provided specific pattern
        all_paths.extend(folder.glob(pattern))
    else:
        # Discover all supported formats (case-insensitive)
        for ext in SUPPORTED_EXTENSIONS:
            all_paths.extend(folder.glob(f"*{ext}"))
            all_paths.extend(folder.glob(f"*{ext.upper()}"))

    # Remove duplicates and sort naturally
    unique_paths = list(set(all_paths))
    sorted_paths = natsort.natsorted(unique_paths, key=lambda p: p.name)
    return sorted_paths


def validate_images(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Pre-validate images for corruption and size constraints.

    Args:
        paths: List of image Path objects to validate.

    Returns:
        (valid_paths, skipped_list)
        - valid_paths: List of Path objects that passed validation
        - skipped_list: List of (path, reason) tuples for images that were skipped

    Note:
        Dimension check: Skip if > 25 MP (25,000,000 pixels)
    """
    valid_paths = []
    skipped = []

    for path in paths:
        try:
            # Open and verify image (checks for corruption)
            with Image.open(path) as img:
                img.verify()

            # Check dimensions (must re-open after verify())
            with Image.open(path) as img:
                width, height = img.size
                megapixels = width * height
                if megapixels > 25_000_000:
                    skipped.append((path, f"Oversized ({width}x{height} = {megapixels:,} MP > 25 MP)"))
                    logger.warning(f"Skipping oversized image: {path.name} ({megapixels:,} MP)")
                    continue

            valid_paths.append(path)
        except Exception as e:
            reason = f"Corrupted or invalid: {type(e).__name__}: {str(e)[:100]}"
            skipped.append((path, reason))
            logger.warning(f"Skipping invalid image: {path.name} — {reason}")

    return valid_paths, skipped


def check_webp_support() -> bool:
    """Check if Pillow has WEBP support.

    Returns:
        True if WEBP codec is available, False otherwise.

    Side Effect:
        Logs warning if WEBP is not available.
    """
    try:
        has_webp = features.check_codec("webp")
    except ValueError:
        # Pillow >=10 removed "webp" from check_codec; use check_module instead.
        has_webp = features.check_module("webp")
    if not has_webp:
        logger.warning(
            "WEBP support not available in Pillow. "
            "WEBP images will fail validation. "
            "Install Pillow with libwebp support (usually automatic on modern systems)."
        )
    return has_webp
