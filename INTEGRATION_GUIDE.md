# NAS Downloader — Integration Guide (English & Vietnamese)

This document describes the public API, data contracts, and integration patterns for **NAS Downloader** (package `nas_spec_sync`), intended for developers and AI agents integrating this downloader/extractor with a downstream consumer module.

---

## 1. Overview & Guarantees

NAS Downloader connects to a NAS over **SFTP** or **FTPS**, monitors a configured `remote_dir` (e.g., `/data`), filters subfolders and files using wildcard/substring patterns, downloads new or updated files, extracts archives locally (while keeping direct documents intact), and deletes local archives afterward.

### Key Behavioral Guarantees for Downstream Modules
1. **Archive vs. Direct Document Support**:
   - **Archive files** (`.zip`, `.7z`, `.rar`, `.tar`, `.tgz`): Extracted safely into `<download_dir>/<folder_name>/<stem>/`. The downloaded archive file on local disk is deleted (when `keep_archive_after_extract` is `false`). `item.extracted_dir` points to the extracted folder.
   - **Direct files / Documents** (Excel `.xlsx`/`.xls`, PDF `.pdf`, CSV `.csv`, Word `.docx`, etc.): **Kept intact without extraction**. Saved directly to `<download_dir>/<folder_name>/<filename>`. `item.extracted_dir` points directly to the saved file.
2. **Only Completed Data Is Reported**: A file is only emitted to callbacks, `SyncResult.processed_items`, `manifest_file`, and `webhook_url` **after**:
   - Stability check passes (file size & `mtime` on NAS remain unchanged across `stability_wait_seconds`, preventing partial reads of 500–600MB uploads).
   - Downloaded byte size matches remote file size.
   - Archive extraction (if applicable) succeeds into a staging directory and is atomically swapped (`os.replace`) into `extracted_dir`.
   - Local archive is deleted (when `keep_archive_after_extract` is `false`).
   - State is persisted in `state_file`.
3. **Delta-Only Reporting**: `SyncResult.processed_items` only contains files newly downloaded or overwritten in **the current cycle**. Previously processed unchanged files are skipped.
4. **Overwrite Detection**: If a file on the NAS is overwritten (different `size` or `mtime` >= 1s difference), it is re-downloaded, atomically replaces the old `extracted_dir`, and is emitted with `is_overwrite = True`.

---

## 2. Public Python API

Import from the `nas_spec_sync` package (ensure the project directory is on `PYTHONPATH` or installed as a local package):

```python
from nas_spec_sync import (
    NasSpecSync,
    ProcessedItem,
    SyncResult,
    AppConfig,
    load_config,
    load_config_from_dict,
)
```

### Core Class: `NasSpecSync`

| Method | Signature | Description |
| :--- | :--- | :--- |
| `from_config_file` | `NasSpecSync.from_config_file(config_path: str) -> NasSpecSync` | Instantiate from a `.json` or `.yaml`/`.yml` config file. |
| `__init__` | `NasSpecSync(config: AppConfig, client_factory=None)` | Instantiate programmatically from an `AppConfig` object. |
| `on_file_ready` | `syncer.on_file_ready(callback: Callable[[ProcessedItem], None]) -> None` | Register a callback invoked immediately after **each** file/archive is ready. |
| `on_batch_complete` | `syncer.on_batch_complete(callback: Callable[[SyncResult], None]) -> None` | Register a callback invoked at the end of **each** sync cycle. |
| `run_once` | `syncer.run_once() -> SyncResult` | Run one synchronous scan/download/extract cycle and return `SyncResult`. |
| `run_forever` | `syncer.run_forever() -> None` | Blocking periodic loop running every `scheduler.interval_seconds`. |
| `stop` | `syncer.stop() -> None` | Gracefully stop `run_forever()` (safe to call from another thread or signal handler). |

---

## 3. Data Contracts

### `ProcessedItem`
Represents one file or archive that was successfully downloaded and prepared in the current cycle.

| Field | Type | Description |
| :--- | :--- | :--- |
| `remote_path` | `str` | Full path on NAS (e.g. `"/data/[101]20261127_update/package.zip"`). |
| `folder_name` | `str` | Matched subfolder name on NAS (e.g. `"[101]20261127_update"`). |
| `file_name` | `str` | File name (e.g. `"package.zip"` or `"report.xlsx"`). |
| `extracted_dir` | `str` | Local directory path (for archives) or local file path (for non-archives like `.xlsx`/`.pdf`). |
| `size` | `int` | File size in bytes on NAS. |
| `mtime` | `float` | Last modified timestamp (Unix epoch seconds) on NAS. |
| `is_overwrite` | `bool` | `True` if this file existed in `state.json` before and was updated; `False` if brand new. |
| `processed_at` | `str` | UTC ISO-8601 timestamp when processing completed. |

### `SyncResult`
Returned by `run_once()` and passed to `on_batch_complete` callbacks.

| Field / Property | Type | Description |
| :--- | :--- | :--- |
| `started_at` | `str` | Cycle start UTC ISO-8601 timestamp. |
| `finished_at` | `str` | Cycle end UTC ISO-8601 timestamp. |
| `has_new_data` | `bool` (property) | `True` if `len(processed_items) > 0`. |
| `processed_items` | `List[ProcessedItem]` | All items downloaded and processed during this cycle. |
| `skipped_unstable` | `List[str]` | Remote paths deferred because size/mtime changed during `stability_wait_seconds`. |
| `errors` | `List[str]` | Error messages encountered during the cycle (empty list if clean). |
| `to_dict()` | `dict` | JSON-serializable dictionary representation. |

#### Example JSON (`SyncResult.to_dict()` / `manifest_file` / `webhook_url` payload)
```json
{
  "started_at": "2026-11-27T14:00:00+00:00",
  "finished_at": "2026-11-27T14:00:08+00:00",
  "has_new_data": true,
  "processed_items": [
    {
      "remote_path": "/data/[101]20261127_update/package.zip",
      "folder_name": "[101]20261127_update",
      "file_name": "package.zip",
      "extracted_dir": "downloads/[101]20261127_update/package",
      "size": 524288000,
      "mtime": 1795788000.0,
      "is_overwrite": false,
      "processed_at": "2026-11-27T14:00:08+00:00"
    }
  ],
  "skipped_unstable": [],
  "errors": []
}
```

---

## 4. Integration Patterns

### Pattern A: Python In-Process Integration (Recommended for Python Modules)

Use this when the downstream module runs in the same Python process.

```python
import sys
from pathlib import Path

# Add project path if not installed as a package
sys.path.insert(0, "/path/to/Nas_download spec")

from nas_spec_sync import NasSpecSync, ProcessedItem, SyncResult


def on_data_ready(item: ProcessedItem) -> None:
    """Called immediately after each file is downloaded (and extracted if archive)."""
    target_path = Path(item.extracted_dir)
    if item.is_overwrite:
        print(f"Re-ingesting updated data: {item.folder_name}/{item.file_name}")
    else:
        print(f"Ingesting new data: {item.folder_name}/{item.file_name}")

    # Pass target_path to your downstream module here
    # downstream_module.process(target_path, folder=item.folder_name, file=item.file_name)


syncer = NasSpecSync.from_config_file("/path/to/Nas_download spec/config.json")
syncer.on_file_ready(on_data_ready)

# Option 1: Single run (e.g., triggered by your app)
result: SyncResult = syncer.run_once()
if result.errors:
    print("Sync errors:", result.errors)

# Option 2: Continuous polling loop
# syncer.run_forever()
```

### Pattern B: Out-of-Process Integration via Manifest File

If the downstream module is a separate process (or written in another language), configure `integration.manifest_file` in `config.json`:

```json
"integration": {
  "webhook_url": null,
  "manifest_file": "./latest_sync_manifest.json"
}
```

NAS Downloader atomically updates `latest_sync_manifest.json` after every cycle using the exact `SyncResult.to_dict()` schema shown in Section 3. The consumer can check `has_new_data` and iterate over `processed_items[*].extracted_dir`.

### Pattern C: Event-Driven Integration via HTTP Webhook

Configure `integration.webhook_url` in `config.json`:

```json
"integration": {
  "webhook_url": "http://localhost:8080/api/data-ready",
  "manifest_file": null
}
```

Whenever `result.has_new_data` is `True`, NAS Downloader sends an HTTP `POST` request with `Content-Type: application/json` containing the `SyncResult.to_dict()` payload.

---

## 5. Configuration Reference (`config.json` / `config.yaml`)

- `nas.protocol`: `"sftp"` or `"ftps"`.
- `nas.port`: Optional integer. Defaults to `22` (SFTP), `21` (explicit FTPS), or `990` (implicit FTPS when `implicit_tls: true`).
- `nas.remote_dir`: Target root directory on NAS (e.g. `"/data"`).
- `patterns.folder_patterns`: List of strings. Substring match if no `*`/`?`, otherwise wildcard fullmatch (e.g. `["update"]` or `["[*]20*_update"]`).
- `patterns.file_patterns`: List of strings (e.g. `["*.zip", "*.7z", "*.xlsx", "*.pdf"]`).
- `patterns.case_sensitive`: Boolean (default `false`).
- `local.download_dir`: Local destination root directory.
- `local.state_file`: Path to JSON state file tracking `(remote_path, size, mtime)`.
- `local.extract_to_subfolder`: Boolean (default `true`). If `true`, `package.zip` extracts to `<download_dir>/<folder_name>/package/`.
- `local.keep_archive_after_extract`: Boolean (default `false` — deletes archive after extraction).
- `concurrency.stability_wait_seconds`: Seconds to wait before re-checking `size` and `mtime` on NAS (default `5`).

---

## 6. Tóm tắt nhanh (Tiếng Việt)

- **Module khác có biết file nào vừa tải & xử lý xong không?** Có. `syncer.run_once()` trả về `SyncResult` chứa danh sách `processed_items` (chỉ gồm các file mới hoặc vừa được ghi đè ở lượt chạy đó), hoặc dùng callback `syncer.on_file_ready(fn)` để nhận từng `ProcessedItem` ngay khi hoàn tất.
- **Đường dẫn dữ liệu đầu ra**: nằm ở `item.extracted_dir` (là thư mục nếu là file nén, hoặc đường dẫn file trực tiếp nếu là Excel/PDF).
- **Phân biệt file mới hay file ghi đè**: kiểm tra `item.is_overwrite` (`True` nếu là bản cập nhật đè).
- **Tích hợp tiến trình khác / ngôn ngữ khác**: đọc `integration.manifest_file` (JSON) hoặc nhận HTTP POST qua `integration.webhook_url`.
