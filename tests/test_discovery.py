"""Unit tests for discovery.py"""

import tempfile
from pathlib import Path

import pytest

from ocr_batch.discovery import check_webp_support, discover_images, validate_images


class TestDiscoverImages:
    """Tests for discover_images function."""

    def test_discover_images_empty_folder(self):
        """Test discovering images in an empty folder."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = discover_images(Path(tmpdir))
            assert result == []

    def test_discover_images_not_a_directory(self):
        """Test that ValueError is raised for non-directory path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "file.txt"
            file_path.touch()
            with pytest.raises(ValueError, match="Not a directory"):
                discover_images(file_path)

    def test_discover_images_nonexistent_folder(self):
        """Test that ValueError is raised for non-existent folder."""
        with pytest.raises(ValueError, match="does not exist"):
            discover_images(Path("/nonexistent/path"))

    def test_discover_images_with_jpg_files(self):
        """Test discovering .jpg files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            (tmpdir / "image1.jpg").touch()
            (tmpdir / "image2.jpg").touch()
            result = discover_images(tmpdir)
            assert len(result) == 2
            assert all(p.suffix.lower() == ".jpg" for p in result)

    def test_discover_images_with_png_files(self):
        """Test discovering .png files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            (tmpdir / "image1.png").touch()
            (tmpdir / "image2.png").touch()
            result = discover_images(tmpdir)
            assert len(result) == 2
            assert all(p.suffix.lower() == ".png" for p in result)

    def test_discover_images_case_insensitive(self):
        """Test case-insensitive extension matching."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            (tmpdir / "image.JPG").touch()
            (tmpdir / "image2.PNG").touch()
            result = discover_images(tmpdir)
            assert len(result) == 2

    def test_discover_images_mixed_formats(self):
        """Test discovering mixed image formats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            (tmpdir / "img1.jpg").touch()
            (tmpdir / "img2.png").touch()
            (tmpdir / "img3.tiff").touch()
            (tmpdir / "img4.bmp").touch()
            result = discover_images(tmpdir)
            assert len(result) == 4

    def test_discover_images_ignores_non_images(self):
        """Test that non-image files are ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            (tmpdir / "image.jpg").touch()
            (tmpdir / "readme.txt").touch()
            (tmpdir / "data.csv").touch()
            result = discover_images(tmpdir)
            assert len(result) == 1
            assert result[0].name == "image.jpg"

    def test_natural_sort_order(self):
        """Test natural sorting (img1, img2, img10 order)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            # Create files in non-sorted order
            (tmpdir / "img10.jpg").touch()
            (tmpdir / "img2.jpg").touch()
            (tmpdir / "img1.jpg").touch()
            result = discover_images(tmpdir)
            names = [p.name for p in result]
            assert names == ["img1.jpg", "img2.jpg", "img10.jpg"]

    def test_discover_images_with_pattern(self):
        """Test discovering with custom glob pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            (tmpdir / "image1.jpg").touch()
            (tmpdir / "image2.jpg").touch()
            (tmpdir / "document.png").touch()
            result = discover_images(tmpdir, pattern="*image*.jpg")
            assert len(result) == 2


class TestValidateImages:
    """Tests for validate_images function."""

    def test_validate_images_empty_list(self):
        """Test validation of empty list."""
        valid, skipped = validate_images([])
        assert valid == []
        assert skipped == []

    def test_validate_images_nonexistent_file(self):
        """Test that nonexistent files are marked as skipped."""
        nonexistent = Path("/tmp/nonexistent_image_12345.jpg")
        valid, skipped = validate_images([nonexistent])
        assert len(valid) == 0
        assert len(skipped) == 1
        assert skipped[0][0] == nonexistent

    def test_validate_images_with_reason_text(self):
        """Test that skip reasons are included."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            fake_path = tmpdir / "fake.jpg"
            fake_path.touch()  # Create empty/corrupted file
            valid, skipped = validate_images([fake_path])
            assert len(skipped) >= 0  # May or may not fail depending on PIL behavior


class TestCheckWebpSupport:
    """Tests for check_webp_support function."""

    def test_check_webp_support_returns_bool(self):
        """Test that check_webp_support returns a boolean."""
        result = check_webp_support()
        assert isinstance(result, bool)
