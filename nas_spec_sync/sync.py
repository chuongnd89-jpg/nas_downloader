"""Core NAS synchronization engine with stability check and integration hooks."""

from datetime import datetime, timezone
import logging
from pathlib import Path
import shutil
import threading
import time
from typing import Callable, List, Optional, Tuple

from .clients import NasClient, RemoteEntry, create_nas_client
from .config import AppConfig, load_config
from .extractor import extract_and_cleanup
from .matcher import match_any
from .notifier import (
    BatchCompleteCallback,
    FileReadyCallback,
    ProcessedItem,
    SyncResult,
    send_webhook,
    write_manifest,
)
from .state import StateStore

logger = logging.getLogger(__name__)


class NasSpecSync:
    """Monitors NAS folders, downloads & extracts new/updated archives, and notifies consumers."""

    def __init__(
        self,
        config: AppConfig,
        client_factory: Optional[Callable[[], NasClient]] = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory or (lambda: create_nas_client(self.config.nas))
        self.state = StateStore(self.config.local.state_file)
        self._file_callbacks: List[FileReadyCallback] = []
        self._batch_callbacks: List[BatchCompleteCallback] = []
        self._stop_event = threading.Event()

    @classmethod
    def from_config_file(
        cls,
        config_path: str,
        client_factory: Optional[Callable[[], NasClient]] = None,
    ) -> "NasSpecSync":
        cfg = load_config(config_path)
        return cls(cfg, client_factory=client_factory)

    def on_file_ready(self, callback: FileReadyCallback) -> None:
        """Register a callback invoked immediately after each file is downloaded and extracted."""
        self._file_callbacks.append(callback)

    def on_batch_complete(self, callback: BatchCompleteCallback) -> None:
        """Register a callback invoked at the end of each sync cycle."""
        self._batch_callbacks.append(callback)

    def stop(self) -> None:
        """Signal the periodic loop to stop gracefully."""
        self._stop_event.set()

    def _is_ignored_temp_file(self, filename: str) -> bool:
        lower = filename.lower()
        return any(lower.endswith(ext.lower()) for ext in self.config.concurrency.ignore_extensions)

    def _collect_candidates(
        self, client: NasClient
    ) -> List[Tuple[RemoteEntry, RemoteEntry, bool]]:
        """Scan matching subfolders and return (folder_entry, file_entry, is_overwrite) candidates."""
        candidates: List[Tuple[RemoteEntry, RemoteEntry, bool]] = []
        root_entries = client.list_dir(self.config.nas.remote_dir)

        for entry in root_entries:
            if not entry.is_dir:
                continue
            if not match_any(
                entry.name,
                self.config.patterns.folder_patterns,
                case_sensitive=self.config.patterns.case_sensitive,
            ):
                continue

            sub_entries = client.list_dir(entry.path)
            for file_entry in sub_entries:
                if file_entry.is_dir:
                    continue
                if self._is_ignored_temp_file(file_entry.name):
                    continue
                if not match_any(
                    file_entry.name,
                    self.config.patterns.file_patterns,
                    case_sensitive=self.config.patterns.case_sensitive,
                ):
                    continue

                if self.state.is_processed_and_unchanged(
                    file_entry.path, file_entry.size, file_entry.mtime
                ):
                    continue

                existing = self.state.get(file_entry.path)
                is_overwrite = existing is not None
                candidates.append((entry, file_entry, is_overwrite))

        return candidates

    def _filter_stable_candidates(
        self,
        client: NasClient,
        candidates: List[Tuple[RemoteEntry, RemoteEntry, bool]],
        result: SyncResult,
    ) -> List[Tuple[RemoteEntry, RemoteEntry, bool]]:
        """Wait briefly and re-stat candidates to avoid downloading files still being written."""
        wait_sec = self.config.concurrency.stability_wait_seconds
        if not candidates or wait_sec <= 0:
            return candidates

        logger.info(
            "Checking stability of %d candidate file(s) (waiting %.1fs)...",
            len(candidates),
            wait_sec,
        )
        time.sleep(wait_sec)

        stable: List[Tuple[RemoteEntry, RemoteEntry, bool]] = []
        for folder_entry, file_entry, is_overwrite in candidates:
            latest = client.stat_file(file_entry.path)
            if latest is None:
                logger.warning("File disappeared during stability check: %s", file_entry.path)
                result.skipped_unstable.append(file_entry.path)
                continue

            if latest.size <= 0:
                logger.info("File size is 0 (likely still writing): %s", file_entry.path)
                result.skipped_unstable.append(file_entry.path)
                continue

            if latest.size != file_entry.size or abs(latest.mtime - file_entry.mtime) >= 1.0:
                logger.info(
                    "File is still being modified on NAS, deferring: %s "
                    "(size %d->%d)",
                    file_entry.path,
                    file_entry.size,
                    latest.size,
                )
                result.skipped_unstable.append(file_entry.path)
                continue

            stable.append((folder_entry, latest, is_overwrite))

        return stable

    def run_once(self) -> SyncResult:
        """Execute one full check, download, extract, and notification cycle."""
        started_at = datetime.now(timezone.utc).isoformat()
        result = SyncResult(started_at=started_at, finished_at=started_at)

        download_root = Path(self.config.local.download_dir)
        tmp_root = download_root / ".tmp"
        download_root.mkdir(parents=True, exist_ok=True)
        tmp_root.mkdir(parents=True, exist_ok=True)

        try:
            with self._client_factory() as client:
                candidates = self._collect_candidates(client)
                stable_candidates = self._filter_stable_candidates(client, candidates, result)

                for folder_entry, file_entry, is_overwrite in stable_candidates:
                    temp_zip = tmp_root / folder_entry.name / f"{file_entry.name}.downloading"
                    staged_zip = tmp_root / folder_entry.name / file_entry.name
                    try:
                        if temp_zip.exists():
                            temp_zip.unlink()
                        if staged_zip.exists():
                            staged_zip.unlink()

                        logger.info("Downloading %s (%d bytes)...", file_entry.path, file_entry.size)
                        client.download_file(file_entry.path, temp_zip)

                        actual_size = temp_zip.stat().st_size
                        if actual_size != file_entry.size:
                            raise IOError(
                                f"Downloaded size mismatch for {file_entry.path}: "
                                f"expected {file_entry.size}, got {actual_size} "
                                "(file may have been overwritten mid-transfer)"
                            )

                        staged_zip.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(temp_zip), str(staged_zip))

                        dest_folder = download_root / folder_entry.name
                        if self.config.local.keep_archive_after_extract:
                            final_zip = dest_folder / file_entry.name
                            dest_folder.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(staged_zip), str(final_zip))
                            archive_to_extract = final_zip
                        else:
                            archive_to_extract = staged_zip

                        extracted_dir = extract_and_cleanup(
                            archive_path=archive_to_extract,
                            dest_folder=dest_folder,
                            extract_to_subfolder=self.config.local.extract_to_subfolder,
                            keep_archive=self.config.local.keep_archive_after_extract,
                        )

                        rec = self.state.mark_processed(
                            remote_path=file_entry.path,
                            size=file_entry.size,
                            mtime=file_entry.mtime,
                            extracted_dir=str(extracted_dir),
                        )

                        item = ProcessedItem(
                            remote_path=file_entry.path,
                            folder_name=folder_entry.name,
                            file_name=file_entry.name,
                            extracted_dir=str(extracted_dir),
                            size=file_entry.size,
                            mtime=file_entry.mtime,
                            is_overwrite=is_overwrite,
                            processed_at=rec.processed_at,
                        )
                        result.processed_items.append(item)
                        logger.info("Processed %s -> %s", file_entry.path, extracted_dir)

                        for cb in self._file_callbacks:
                            try:
                                cb(item)
                            except Exception as cb_exc:
                                logger.exception("on_file_ready callback error: %s", cb_exc)

                    except Exception as exc:
                        err_msg = f"Failed to process {file_entry.path}: {exc}"
                        logger.error(err_msg)
                        result.errors.append(err_msg)
                        if temp_zip.exists():
                            temp_zip.unlink()
                        if staged_zip.exists():
                            staged_zip.unlink()

        except Exception as exc:
            err_msg = f"NAS sync cycle failed: {exc}"
            logger.exception(err_msg)
            result.errors.append(err_msg)

        result.finished_at = datetime.now(timezone.utc).isoformat()

        if self.config.integration.manifest_file:
            try:
                write_manifest(self.config.integration.manifest_file, result)
            except Exception as exc:
                logger.warning("Failed to write manifest: %s", exc)

        if self.config.integration.webhook_url and result.has_new_data:
            send_webhook(self.config.integration.webhook_url, result)

        for cb in self._batch_callbacks:
            try:
                cb(result)
            except Exception as cb_exc:
                logger.exception("on_batch_complete callback error: %s", cb_exc)

        return result

    def run_forever(self) -> None:
        """Run sync cycles periodically until stop() is called or interrupted."""
        self._stop_event.clear()
        interval = self.config.scheduler.interval_seconds
        logger.info("Starting periodic NAS sync (interval: %.1fs)", interval)
        while not self._stop_event.is_set():
            self.run_once()
            if self._stop_event.wait(timeout=interval):
                break
        logger.info("NAS sync stopped.")
