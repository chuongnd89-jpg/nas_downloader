"""Archive extraction and file placement with atomic operations and cleanup."""

import os
from pathlib import Path
import shutil
import subprocess
import zipfile

try:
    import rarfile
except ImportError:
    rarfile = None

try:
    import py7zr
except ImportError:
    py7zr = None


# Known archive extensions that should be extracted
ARCHIVE_EXTENSIONS = {
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
}

# Known document / data extensions that should be kept as-is (NO extraction)
DOCUMENT_EXTENSIONS = {
    ".xlsx",
    ".xls",
    ".xlsm",
    ".xlsb",
    ".csv",
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".txt",
    ".json",
    ".xml",
}


def is_archive(path: Path) -> bool:
    """Return True if the file is a recognized archive format."""
    return path.suffix.lower() in ARCHIVE_EXTENSIONS


def _safe_extract_zip(archive_path: Path, target_dir: Path) -> None:
    """Extract a zip file safely, preventing ZipSlip path traversal."""
    target_resolved = target_dir.resolve()
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.infolist():
            dest_path = (target_dir / member.filename).resolve()
            if not str(dest_path).startswith(str(target_resolved)):
                raise ValueError(f"Unsafe path in zip archive: {member.filename}")
        zf.extractall(target_dir)


def _safe_extract_7z(archive_path: Path, target_dir: Path) -> None:
    """Extract a .7z file safely using py7zr or 7z CLI."""
    if py7zr:
        target_resolved = target_dir.resolve()
        with py7zr.SevenZipFile(archive_path, mode="r") as sz:
            for member in sz.list():
                dest_path = (target_dir / member.filename).resolve()
                if not str(dest_path).startswith(str(target_resolved)):
                    raise ValueError(f"Unsafe path in 7z archive: {member.filename}")
            sz.extractall(target_dir)
        return

    # Fallback to 7z CLI if installed on system
    z_bin = shutil.which("7z") or shutil.which("7za")
    if z_bin:
        res = subprocess.run(
            [z_bin, "x", "-y", f"-o{target_dir}", str(archive_path)],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            return
        raise RuntimeError(f"7z extraction failed: {res.stderr}")

    raise RuntimeError(
        f"Cannot extract 7z file '{archive_path.name}'. 'py7zr' library is required."
    )


def _safe_extract_rar(archive_path: Path, target_dir: Path) -> None:
    """Extract a .rar file using unrar/unrar-free CLI, bsdtar, or rarfile."""
    # 1. Check unrar / unrar-free (standard on Ubuntu / Debian)
    unrar_bin = shutil.which("unrar") or shutil.which("unrar-free") or shutil.which("rar")
    if unrar_bin:
        res = subprocess.run(
            [unrar_bin, "x", "-o+", str(archive_path), str(target_dir)],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            return

    # 2. Check 7z CLI (p7zip-full handles RAR extraction seamlessly on Linux)
    z_bin = shutil.which("7z") or shutil.which("7za")
    if z_bin:
        res = subprocess.run(
            [z_bin, "x", "-y", f"-o{target_dir}", str(archive_path)],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            return

    # 3. Check bsdtar (tar)
    tar_bin = shutil.which("tar")
    if tar_bin:
        res = subprocess.run(
            [tar_bin, "-xf", str(archive_path), "-C", str(target_dir)],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            return

    # 4. Check rarfile python library
    if rarfile:
        try:
            target_resolved = target_dir.resolve()
            with rarfile.RarFile(archive_path) as rf:
                for member in rf.infolist():
                    dest_path = (target_dir / member.filename).resolve()
                    if not str(dest_path).startswith(str(target_resolved)):
                        raise ValueError(f"Unsafe path in archive: {member.filename}")
                rf.extractall(target_dir)
            return
        except Exception as e:
            raise RuntimeError(f"Failed to extract RAR file {archive_path.name}: {e}")

    raise RuntimeError(
        f"Cannot extract RAR file '{archive_path.name}'. "
        "Please install 'unrar' or 'p7zip-full' (sudo apt-get install p7zip-full unrar)."
    )


def _safe_extract_archive(archive_path: Path, target_dir: Path) -> None:
    """Extract zip, 7z, rar, tar, or other supported archives safely."""
    suffix = archive_path.suffix.lower()

    if suffix == ".zip":
        _safe_extract_zip(archive_path, target_dir)
        return
    if suffix == ".7z":
        _safe_extract_7z(archive_path, target_dir)
        return
    if suffix == ".rar":
        _safe_extract_rar(archive_path, target_dir)
        return
    if suffix in (".tar", ".tgz", ".gz", ".bz2", ".xz"):
        import tarfile

        with tarfile.open(archive_path) as tf:
            tf.extractall(target_dir)
        return

    # Fallback to zipfile
    _safe_extract_zip(archive_path, target_dir)


def extract_and_cleanup(
    archive_path: Path,
    dest_folder: Path,
    extract_to_subfolder: bool = True,
    keep_archive: bool = False,
) -> Path:
    """Extract archive or move non-archive document safely into destination.

    - For archives (.zip, .7z, .rar, etc.):
        Extracts safely into `<dest_folder>/<stem>/` (or directly into `<dest_folder>/`).
        Deletes archive file if keep_archive is False.
        Returns the directory Path containing extracted files.
    - For non-archives (Excel .xlsx, PDF .pdf, CSV .csv, TXT, etc.):
        Leaves the file intact (NO extraction).
        Atomically copies/moves the file into `<dest_folder>/<filename>`.
        Returns the Path to the saved file (e.g. `<dest_folder>/report.xlsx`).
    """
    if not archive_path.exists():
        raise FileNotFoundError(f"File not found: {archive_path}")

    dest_folder.mkdir(parents=True, exist_ok=True)

    # CASE 1: Non-archive file (e.g. Excel, PDF, CSV, Word) -> DO NOT EXTRACT
    if not is_archive(archive_path):
        final_file = dest_folder / archive_path.name
        temp_staging = dest_folder / f".{archive_path.name}_staging_{os.getpid()}"
        if temp_staging.exists():
            temp_staging.unlink()

        shutil.copy2(str(archive_path), str(temp_staging))
        if final_file.exists():
            final_file.unlink()
        os.replace(temp_staging, final_file)

        if not keep_archive and archive_path.exists():
            archive_path.unlink()
        return final_file

    # CASE 2: Archive file (.zip, .7z, .rar, .tar, etc.) -> EXTRACT
    if extract_to_subfolder:
        final_dir = dest_folder / archive_path.stem
        staging_dir = dest_folder / f".{archive_path.stem}_extracting_{os.getpid()}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            _safe_extract_archive(archive_path, staging_dir)
            if final_dir.exists():
                backup_dir = dest_folder / f".{archive_path.stem}_old_{os.getpid()}"
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                os.replace(final_dir, backup_dir)
                try:
                    os.replace(staging_dir, final_dir)
                except Exception:
                    os.replace(backup_dir, final_dir)
                    raise
                shutil.rmtree(backup_dir, ignore_errors=True)
            else:
                os.replace(staging_dir, final_dir)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise
    else:
        final_dir = dest_folder
        _safe_extract_archive(archive_path, final_dir)

    if not keep_archive and archive_path.exists():
        archive_path.unlink()

    return final_dir
