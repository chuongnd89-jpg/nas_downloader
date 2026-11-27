"""Example of integrating NAS Downloader with another Python module."""

import logging
from pathlib import Path

from nas_spec_sync import NasSpecSync, ProcessedItem, SyncResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def downstream_process_data(item: ProcessedItem) -> None:
    """Example consumer function invoked right after each file/archive is ready."""
    target_path = Path(item.extracted_dir)
    print(f"[Consumer] New data ready at {target_path} (overwrite={item.is_overwrite})")
    if target_path.is_dir():
        for f in target_path.rglob("*"):
            if f.is_file():
                print(f"  - {f.relative_to(target_path)}")
    elif target_path.is_file():
        print(f"  - {target_path.name} ({target_path.stat().st_size} bytes)")


def on_cycle_done(result: SyncResult) -> None:
    """Example batch-level hook invoked at the end of each sync cycle."""
    if result.has_new_data:
        print(f"[Consumer] Batch finished with {len(result.processed_items)} updated item(s).")
    if result.skipped_unstable:
        print(f"[Consumer] Deferred {len(result.skipped_unstable)} file(s) still uploading to NAS.")


if __name__ == "__main__":
    syncer = NasSpecSync.from_config_file("config.example.json")
    syncer.on_file_ready(downstream_process_data)
    syncer.on_batch_complete(on_cycle_done)

    # Run a single check (or call syncer.run_forever() for continuous polling)
    sync_result = syncer.run_once()
    print("Summary:", sync_result.to_dict())
