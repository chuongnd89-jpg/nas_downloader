"""Command-line interface for NAS Downloader."""

import argparse
import logging
from pathlib import Path
import signal
import sys

from nas_spec_sync import NasSpecSync, configure_logging, load_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="NAS Downloader: Monitor NAS folders (SFTP/FTPS), download & extract new/updated files."
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.json",
        help="Path to JSON or YAML config file (default: config.json)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sync cycle and exit.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging (overrides config log_level to DEBUG).",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Explicit log file path (overrides config.local.log_file).",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    log_file = args.log_file or cfg.local.log_file
    configure_logging(
        log_file=log_file,
        log_level=cfg.local.log_level,
        verbose=args.verbose,
    )

    logger = logging.getLogger("cli")
    logger.info("Initializing NAS Downloader with config: %s", args.config)
    if log_file:
        logger.info("Logging to file: %s", Path(log_file).resolve())

    syncer = NasSpecSync(cfg)

    if args.once:
        result = syncer.run_once()
        logger.info(
            "Cycle complete. Processed=%d, Unstable=%d, Errors=%d",
            len(result.processed_items),
            len(result.skipped_unstable),
            len(result.errors),
        )
        return 1 if result.errors else 0

    def _handle_sig(signum, frame):
        logger.info("Received signal %s, stopping...", signum)
        syncer.stop()

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    syncer.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
