"""Integration models, callbacks, manifest writer, and webhook notifier."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Callable, List, Optional
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


@dataclass
class ProcessedItem:
    remote_path: str
    folder_name: str
    file_name: str
    extracted_dir: str
    size: int
    mtime: float
    is_overwrite: bool
    processed_at: str


@dataclass
class SyncResult:
    started_at: str
    finished_at: str
    processed_items: List[ProcessedItem] = field(default_factory=list)
    skipped_unstable: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def has_new_data(self) -> bool:
        return len(self.processed_items) > 0

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "has_new_data": self.has_new_data,
            "processed_items": [asdict(item) for item in self.processed_items],
            "skipped_unstable": self.skipped_unstable,
            "errors": self.errors,
        }


FileReadyCallback = Callable[[ProcessedItem], None]
BatchCompleteCallback = Callable[[SyncResult], None]


def write_manifest(manifest_path: str, result: SyncResult) -> None:
    """Atomically write the latest sync result to a JSON manifest file."""
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def send_webhook(webhook_url: str, result: SyncResult, timeout_seconds: int = 10) -> None:
    """POST SyncResult JSON payload to a configured webhook URL."""
    payload = json.dumps(result.to_dict(), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            logger.info("Webhook %s responded with status %s", webhook_url, resp.status)
    except urllib.error.URLError as exc:
        logger.warning("Failed to send webhook to %s: %s", webhook_url, exc)
