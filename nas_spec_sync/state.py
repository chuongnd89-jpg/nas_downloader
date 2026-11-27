"""Persistent state tracking for processed NAS files."""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Dict, Optional


@dataclass
class FileRecord:
    remote_path: str
    size: int
    mtime: float
    extracted_dir: str
    processed_at: str


class StateStore:
    """Tracks processed remote files by path, size, and mtime."""

    def __init__(self, state_file: str) -> None:
        self.path = Path(state_file)
        self._records: Dict[str, FileRecord] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._records = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._records = {}
            return

        files = raw.get("files", {}) if isinstance(raw, dict) else {}
        self._records = {}
        for key, val in files.items():
            if isinstance(val, dict):
                self._records[key] = FileRecord(
                    remote_path=val.get("remote_path", key),
                    size=int(val.get("size", 0)),
                    mtime=float(val.get("mtime", 0.0)),
                    extracted_dir=str(val.get("extracted_dir", "")),
                    processed_at=str(val.get("processed_at", "")),
                )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "files": {k: asdict(v) for k, v in self._records.items()},
        }
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, self.path)

    def get(self, remote_path: str) -> Optional[FileRecord]:
        return self._records.get(remote_path)

    def is_processed_and_unchanged(self, remote_path: str, size: int, mtime: float) -> bool:
        rec = self._records.get(remote_path)
        if rec is None:
            return False
        # Compare size exactly and mtime within 1 second tolerance (for FTP/SFTP precision)
        return rec.size == size and abs(rec.mtime - mtime) < 1.0

    def mark_processed(
        self, remote_path: str, size: int, mtime: float, extracted_dir: str
    ) -> FileRecord:
        rec = FileRecord(
            remote_path=remote_path,
            size=size,
            mtime=mtime,
            extracted_dir=extracted_dir,
            processed_at=datetime.now(timezone.utc).isoformat(),
        )
        self._records[remote_path] = rec
        self.save()
        return rec
