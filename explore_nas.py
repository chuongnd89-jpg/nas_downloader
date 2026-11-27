"""Interactive explorer to find exact remote_dir path on NAS."""

import argparse
import logging

from nas_spec_sync import create_nas_client, load_config
from nas_spec_sync.logger_setup import configure_logging


def explore_nas(config_path: str, target_path: str = "") -> None:
    configure_logging(log_level="INFO", verbose=False)
    logger = logging.getLogger("nas_explorer")

    cfg = load_config(config_path)
    logger.info("Connecting to %s@%s...", cfg.nas.username, cfg.nas.host)

    with create_nas_client(cfg.nas) as client:
        logger.info("Connected successfully!\n")

        if target_path:
            print(f"[INFO] Listing target directory '{target_path}':")
            items = client.list_dir(target_path)
            for e in items:
                print(f"  {'[DIR] ' if e.is_dir else '[FILE]'} {e.name} (size={e.size}, path={e.path})")
            return

        # Try user default directory first
        try:
            home_entries = client.list_dir(".")
            print("[INFO] Files/Folders in user default directory ('.'):")
            for e in home_entries:
                print(f"  {'[DIR] ' if e.is_dir else '[FILE]'} {e.name} (path: {e.path})")
            print("-" * 60)
        except Exception as exc:
            print(f"[WARN] Could not list '.': {exc}")

        # Try common root candidates
        candidates = ["/", "/volume1", "/volume2", "/shared", "/data", cfg.nas.remote_dir]
        # Deduplicate preserving order
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c and c not in seen:
                seen.add(c)
                unique_candidates.append(c)

        print("[INFO] Checking common directory candidates:")
        for cand in unique_candidates:
            try:
                items = client.list_dir(cand)
                sub_dirs = [it.name for it in items if it.is_dir][:5]
                print(f"  [OK] EXISTS: '{cand}' -> contains {len(items)} items. Subdirs: {sub_dirs}")
            except Exception:
                print(f"  [--] NOT FOUND: '{cand}'")

        print("-" * 60)
        print("Tip: Set the matching path above as 'remote_dir' in config.json.")
        print("     Or run: python explore_nas.py -c config.json -p /your/path")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explore directories on NAS to locate exact path.")
    parser.add_argument("-c", "--config", default="config.json", help="Path to config file")
    parser.add_argument("-p", "--path", default="", help="Optional specific remote path to list")
    args = parser.parse_args()
    explore_nas(args.config, target_path=args.path)
