"""Quick connectivity test script for NAS SFTP/FTPS."""

import argparse
import logging
from pathlib import Path
import sys

from nas_spec_sync import create_nas_client, load_config
from nas_spec_sync.logger_setup import configure_logging


def test_connection(config_path: str) -> bool:
    configure_logging(log_level="DEBUG", verbose=True)
    logger = logging.getLogger("test_nas")

    logger.info("Reading config from %s...", config_path)
    cfg = load_config(config_path)

    logger.info("Attempting connection to NAS (%s://%s:%s)...", cfg.nas.protocol, cfg.nas.host, cfg.nas.resolved_port())
    try:
        with create_nas_client(cfg.nas) as client:
            logger.info(">>> SUCCESS: Connected and authenticated successfully to %s!", cfg.nas.host)
            logger.info("Listing root remote directory: '%s'...", cfg.nas.remote_dir)
            entries = client.list_dir(cfg.nas.remote_dir)
            logger.info("Found %d item(s) in '%s':", len(entries), cfg.nas.remote_dir)
            for entry in entries[:20]:
                kind = "DIR " if entry.is_dir else "FILE"
                logger.info("  [%s] %s (size=%s bytes)", kind, entry.name, entry.size)
            if len(entries) > 20:
                logger.info("  ... and %d more items.", len(entries) - 20)
            return True
    except Exception as exc:
        logger.error(">>> FAILED to connect to NAS: %s", exc, exc_info=True)
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test SFTP/FTPS connection to NAS.")
    parser.add_argument("-c", "--config", default="config.json", help="Path to config file")
    args = parser.parse_args()
    success = test_connection(args.config)
    sys.exit(0 if success else 1)
