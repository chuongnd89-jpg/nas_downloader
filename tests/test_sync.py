"""Unit and integration tests for NAS Downloader (nas_spec_sync)."""

import io
from pathlib import Path
import posixpath
import tempfile
import unittest
import zipfile

import py7zr

from nas_spec_sync import NasClient, NasSpecSync, RemoteEntry, load_config_from_dict
from nas_spec_sync.matcher import match_any, match_pattern


def _make_zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_7z_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, mode="w") as sz:
        for name, content in files.items():
            sz.writestr(content, name)
    return buf.getvalue()


class MockNasClient(NasClient):
    """In-memory mock NAS client simulating SFTP/FTPS directory tree and concurrency."""

    def __init__(self, tree: dict) -> None:
        self.tree = tree
        self.connected = False
        self.download_calls = []

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def list_dir(self, remote_path: str):
        norm = remote_path.rstrip("/") or "/"
        entries = []
        for path, meta in self.tree.items():
            parent = posixpath.dirname(path.rstrip("/")) or "/"
            if parent == norm and path != norm:
                size = len(meta.get("data", b""))
                entries.append(
                    RemoteEntry(
                        name=posixpath.basename(path),
                        path=path,
                        is_dir=meta.get("is_dir", False),
                        size=size,
                        mtime=meta.get("mtime", 1000.0),
                    )
                )
        return entries

    def stat_file(self, remote_path: str):
        meta = self.tree.get(remote_path)
        if meta is None:
            return None
        grow = meta.get("grow_on_stat", 0)
        if grow > 0:
            meta["data"] = meta["data"] + (b"x" * grow)
            meta["grow_on_stat"] = 0
        return RemoteEntry(
            name=posixpath.basename(remote_path),
            path=remote_path,
            is_dir=meta.get("is_dir", False),
            size=len(meta.get("data", b"")),
            mtime=meta.get("mtime", 1000.0),
        )

    def download_file(self, remote_path: str, local_path: Path) -> None:
        self.download_calls.append(remote_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.tree[remote_path]["data"])


class TestMatcher(unittest.TestCase):
    def test_substring_and_wildcard(self):
        self.assertTrue(match_pattern("[100]20261120_update", "update"))
        self.assertTrue(match_pattern("[100]20261120_update", "[100]*_update"))
        self.assertFalse(match_pattern("[100]20261120_update", "[101]*"))
        self.assertTrue(match_any("package_a.zip", ["package_a.zip", "package_b.zip"]))
        self.assertTrue(match_any("PACKAGE_A.zip", ["package_a.zip"], case_sensitive=False))
        self.assertFalse(match_any("PACKAGE_A.zip", ["package_a.zip"], case_sensitive=True))


class TestNasDownloader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _build_config(self, file_patterns=None, stability_wait: float = 0.01):
        if file_patterns is None:
            file_patterns = ["package_a.zip", "package_b.zip", "*.7z", "*.xlsx", "*.pdf"]
        return load_config_from_dict(
            {
                "nas": {
                    "protocol": "sftp",
                    "host": "nas.local",
                    "username": "user",
                    "remote_dir": "/data",
                },
                "patterns": {
                    "folder_patterns": ["update"],
                    "file_patterns": file_patterns,
                    "case_sensitive": False,
                },
                "local": {
                    "download_dir": str(self.root / "downloads"),
                    "state_file": str(self.root / "state.json"),
                    "extract_to_subfolder": True,
                    "keep_archive_after_extract": False,
                },
                "concurrency": {
                    "stability_wait_seconds": stability_wait,
                },
                "integration": {
                    "manifest_file": str(self.root / "manifest.json"),
                },
            }
        )

    def test_sync_extract_cleanup_and_skip_duplicate(self):
        pkg_a_zip = _make_zip_bytes({"readme.txt": "alpha v1"})
        pkg_b_zip = _make_zip_bytes({"data.md": "beta v1"})
        tree = {
            "/data/[100]20261120_update": {"is_dir": True},
            "/data/[100]20261120_update/package_a.zip": {
                "is_dir": False,
                "data": pkg_a_zip,
                "mtime": 1000.0,
            },
            "/data/[100]20261120_update/notes.txt": {
                "is_dir": False,
                "data": b"ignore",
                "mtime": 1000.0,
            },
            "/data/[100]20261120_update/package_a.zip.part": {
                "is_dir": False,
                "data": b"partial",
                "mtime": 1000.0,
            },
            "/data/[101]20261127_update": {"is_dir": True},
            "/data/[101]20261127_update/package_b.zip": {
                "is_dir": False,
                "data": pkg_b_zip,
                "mtime": 2000.0,
            },
        }
        client = MockNasClient(tree)
        cfg = self._build_config()
        ready_events = []

        syncer = NasSpecSync(cfg, client_factory=lambda: client)
        syncer.on_file_ready(ready_events.append)

        res1 = syncer.run_once()
        self.assertEqual(len(res1.processed_items), 2)
        extracted_a = self.root / "downloads" / "[100]20261120_update" / "package_a" / "readme.txt"
        self.assertTrue(extracted_a.exists())

        # Archive must be deleted
        self.assertFalse((self.root / "downloads" / "[100]20261120_update" / "package_a.zip").exists())

    def test_7z_archive_extraction(self):
        seven_z_data = _make_7z_bytes({"doc.txt": "content inside 7z"})
        tree = {
            "/data/[100]20261120_update": {"is_dir": True},
            "/data/[100]20261120_update/Bundle.7z": {
                "is_dir": False,
                "data": seven_z_data,
                "mtime": 1000.0,
            },
        }
        client = MockNasClient(tree)
        cfg = self._build_config(file_patterns=["*.7z"])
        syncer = NasSpecSync(cfg, client_factory=lambda: client)
        res = syncer.run_once()
        self.assertEqual(len(res.processed_items), 1)

        extracted_file = (
            self.root / "downloads" / "[100]20261120_update" / "Bundle" / "doc.txt"
        )
        self.assertTrue(extracted_file.exists())
        self.assertEqual(extracted_file.read_text(), "content inside 7z")
        # 7z archive should be cleaned up
        self.assertFalse(
            (self.root / "downloads" / "[100]20261120_update" / "Bundle.7z").exists()
        )

    def test_excel_and_pdf_non_archive_saved_intact(self):
        excel_dummy_bytes = b"FAKE_EXCEL_DATA_XLSX"
        pdf_dummy_bytes = b"%PDF-1.4 fake pdf stream"
        tree = {
            "/data/[100]20261120_update": {"is_dir": True},
            "/data/[100]20261120_update/Report.xlsx": {
                "is_dir": False,
                "data": excel_dummy_bytes,
                "mtime": 1000.0,
            },
            "/data/[100]20261120_update/Manual.pdf": {
                "is_dir": False,
                "data": pdf_dummy_bytes,
                "mtime": 1000.0,
            },
        }
        client = MockNasClient(tree)
        cfg = self._build_config(file_patterns=["*.xlsx", "*.pdf"])
        syncer = NasSpecSync(cfg, client_factory=lambda: client)
        res = syncer.run_once()

        self.assertEqual(len(res.processed_items), 2)
        excel_local = self.root / "downloads" / "[100]20261120_update" / "Report.xlsx"
        pdf_local = self.root / "downloads" / "[100]20261120_update" / "Manual.pdf"

        self.assertTrue(excel_local.is_file())
        self.assertEqual(excel_local.read_bytes(), excel_dummy_bytes)

        self.assertTrue(pdf_local.is_file())
        self.assertEqual(pdf_local.read_bytes(), pdf_dummy_bytes)

        item_paths = {item.file_name: item.extracted_dir for item in res.processed_items}
        self.assertEqual(item_paths["Report.xlsx"], str(excel_local))
        self.assertEqual(item_paths["Manual.pdf"], str(pdf_local))


if __name__ == "__main__":
    unittest.main()
