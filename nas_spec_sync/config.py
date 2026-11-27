"""Configuration loading and validation for NAS Spec Sync."""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass
class NasConfig:
    protocol: str = "sftp"  # "sftp" or "ftps"
    host: str = ""
    port: Optional[int] = None
    username: str = ""
    password: Optional[str] = None
    private_key_path: Optional[str] = None
    private_key_passphrase: Optional[str] = None
    implicit_tls: bool = False
    remote_dir: str = "/"
    timeout_seconds: int = 30

    def resolved_port(self) -> int:
        if self.port is not None:
            return int(self.port)
        if self.protocol.lower() == "sftp":
            return 22
        return 990 if self.implicit_tls else 21


@dataclass
class PatternConfig:
    folder_patterns: List[str] = field(default_factory=list)
    file_patterns: List[str] = field(default_factory=list)
    case_sensitive: bool = False


@dataclass
class LocalConfig:
    download_dir: str = "./downloads"
    state_file: str = "./state.json"
    log_file: Optional[str] = "./logs/nas_sync.log"
    log_level: str = "INFO"
    extract_to_subfolder: bool = True
    keep_archive_after_extract: bool = False


@dataclass
class ConcurrencyConfig:
    stability_wait_seconds: float = 5.0
    ignore_extensions: List[str] = field(
        default_factory=lambda: [".part", ".tmp", ".filepart", ".downloading"]
    )


@dataclass
class SchedulerConfig:
    interval_seconds: float = 300.0


@dataclass
class IntegrationConfig:
    webhook_url: Optional[str] = None
    manifest_file: Optional[str] = None


@dataclass
class AppConfig:
    nas: NasConfig
    patterns: PatternConfig
    local: LocalConfig = field(default_factory=LocalConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    integration: IntegrationConfig = field(default_factory=IntegrationConfig)


def _validate_config(cfg: AppConfig) -> None:
    proto = cfg.nas.protocol.lower().strip()
    if proto not in ("sftp", "ftps"):
        raise ValueError(f"Unsupported protocol '{cfg.nas.protocol}'. Expected 'sftp' or 'ftps'.")
    cfg.nas.protocol = proto

    if not cfg.nas.host:
        raise ValueError("nas.host is required.")
    if not cfg.nas.username:
        raise ValueError("nas.username is required.")
    if not cfg.nas.remote_dir:
        raise ValueError("nas.remote_dir is required.")
    if not cfg.patterns.folder_patterns:
        raise ValueError("patterns.folder_patterns must contain at least one pattern.")
    if not cfg.patterns.file_patterns:
        raise ValueError("patterns.file_patterns must contain at least one pattern.")
    if cfg.concurrency.stability_wait_seconds < 0:
        raise ValueError("concurrency.stability_wait_seconds cannot be negative.")
    if cfg.scheduler.interval_seconds <= 0:
        raise ValueError("scheduler.interval_seconds must be > 0.")


def load_config_from_dict(data: Dict[str, Any]) -> AppConfig:
    nas_raw = data.get("nas", {})
    patterns_raw = data.get("patterns", {})
    local_raw = data.get("local", {})
    conc_raw = data.get("concurrency", {})
    sched_raw = data.get("scheduler", {})
    integ_raw = data.get("integration", {})

    # Backward-compatible support if user specifies single folder_pattern string
    folder_patterns = patterns_raw.get("folder_patterns")
    if folder_patterns is None and "folder_pattern" in patterns_raw:
        folder_patterns = [patterns_raw["folder_pattern"]]

    cfg = AppConfig(
        nas=NasConfig(**nas_raw),
        patterns=PatternConfig(
            folder_patterns=list(folder_patterns or []),
            file_patterns=list(patterns_raw.get("file_patterns", [])),
            case_sensitive=bool(patterns_raw.get("case_sensitive", False)),
        ),
        local=LocalConfig(**local_raw),
        concurrency=ConcurrencyConfig(**conc_raw),
        scheduler=SchedulerConfig(**sched_raw),
        integration=IntegrationConfig(**integ_raw),
    )
    _validate_config(cfg)
    return cfg


def load_config(path: str) -> AppConfig:
    """Load configuration from a JSON or YAML file."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")

    raw_text = cfg_path.read_text(encoding="utf-8")
    suffix = cfg_path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(raw_text) or {}
    else:
        data = json.loads(raw_text)

    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON/YAML object.")
    return load_config_from_dict(data)
