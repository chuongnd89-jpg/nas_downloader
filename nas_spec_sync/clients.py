"""NAS client abstractions and SFTP / FTPS implementations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
import ftplib
from pathlib import Path
import posixpath
import socket
import ssl
import stat as stat_mod
from typing import List, Optional

import paramiko

from .config import NasConfig


@dataclass
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


class NasClient(ABC):
    """Abstract base class for NAS file transfer clients."""

    @abstractmethod
    def connect(self) -> None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass

    @abstractmethod
    def list_dir(self, remote_path: str) -> List[RemoteEntry]:
        pass

    @abstractmethod
    def stat_file(self, remote_path: str) -> Optional[RemoteEntry]:
        pass

    @abstractmethod
    def download_file(self, remote_path: str, local_path: Path) -> None:
        pass

    def __enter__(self) -> "NasClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class SftpClient(NasClient):
    """SFTP client powered by Paramiko."""

    def __init__(self, config: NasConfig) -> None:
        self.config = config
        self._transport: Optional[paramiko.Transport] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    def connect(self) -> None:
        host = self.config.host
        port = self.config.resolved_port()
        sock = socket.create_connection((host, port), timeout=self.config.timeout_seconds)
        transport = paramiko.Transport(sock)

        pkey = None
        if self.config.private_key_path:
            key_path = str(Path(self.config.private_key_path).expanduser())
            for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
                try:
                    pkey = key_cls.from_private_key_file(
                        key_path, password=self.config.private_key_passphrase
                    )
                    break
                except paramiko.SSHException:
                    continue
            if pkey is None:
                transport.close()
                raise ValueError(f"Could not load private key from {key_path}")

        transport.connect(
            username=self.config.username,
            password=self.config.password,
            pkey=pkey,
        )
        self._transport = transport
        self._sftp = paramiko.SFTPClient.from_transport(transport)
        if self._sftp is None:
            raise RuntimeError("Failed to open SFTP channel.")

    def close(self) -> None:
        if self._sftp is not None:
            try:
                self._sftp.close()
            finally:
                self._sftp = None
        if self._transport is not None:
            try:
                self._transport.close()
            finally:
                self._transport = None

    def list_dir(self, remote_path: str) -> List[RemoteEntry]:
        if self._sftp is None:
            raise RuntimeError("SFTP client is not connected.")
        entries: List[RemoteEntry] = []
        for attr in self._sftp.listdir_attr(remote_path):
            name = attr.filename
            if name in (".", ".."):
                continue
            full_path = posixpath.join(remote_path, name)
            is_dir = bool(attr.st_mode is not None and stat_mod.S_ISDIR(attr.st_mode))
            entries.append(
                RemoteEntry(
                    name=name,
                    path=full_path,
                    is_dir=is_dir,
                    size=int(attr.st_size or 0),
                    mtime=float(attr.st_mtime or 0.0),
                )
            )
        return entries

    def stat_file(self, remote_path: str) -> Optional[RemoteEntry]:
        if self._sftp is None:
            raise RuntimeError("SFTP client is not connected.")
        try:
            attr = self._sftp.stat(remote_path)
        except FileNotFoundError:
            return None
        is_dir = bool(attr.st_mode is not None and stat_mod.S_ISDIR(attr.st_mode))
        return RemoteEntry(
            name=posixpath.basename(remote_path),
            path=remote_path,
            is_dir=is_dir,
            size=int(attr.st_size or 0),
            mtime=float(attr.st_mtime or 0.0),
        )

    def download_file(self, remote_path: str, local_path: Path) -> None:
        if self._sftp is None:
            raise RuntimeError("SFTP client is not connected.")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._sftp.get(remote_path, str(local_path))


class _ImplicitFTP_TLS(ftplib.FTP_TLS):
    """FTP_TLS subclass supporting implicit TLS on port 990."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host)
        return conn, size


def _parse_ftp_modify_time(modify_str: str) -> float:
    # MLSD modify format: YYYYMMDDHHMMSS or YYYYMMDDHHMMSS.sss
    clean = modify_str.split(".")[0]
    try:
        dt = datetime.strptime(clean, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


class FtpsClient(NasClient):
    """FTPS client using ftplib.FTP_TLS (explicit or implicit TLS)."""

    def __init__(self, config: NasConfig) -> None:
        self.config = config
        self._ftp: Optional[ftplib.FTP_TLS] = None

    def connect(self) -> None:
        host = self.config.host
        port = self.config.resolved_port()
        timeout = self.config.timeout_seconds

        if self.config.implicit_tls:
            ftp = _ImplicitFTP_TLS()
            ftp.connect(host=host, port=port, timeout=timeout)
            ftp.sock = ftp.context.wrap_socket(ftp.sock, server_hostname=host)
            ftp.file = ftp.sock.makefile("r", encoding=ftp.encoding)
            ftp.login(user=self.config.username, passwd=self.config.password or "")
            ftp.prot_p()
        else:
            ftp = ftplib.FTP_TLS()
            ftp.connect(host=host, port=port, timeout=timeout)
            ftp.auth()
            ftp.login(user=self.config.username, passwd=self.config.password or "")
            ftp.prot_p()

        ftp.set_pasv(True)
        self._ftp = ftp

    def close(self) -> None:
        if self._ftp is not None:
            try:
                self._ftp.quit()
            except Exception:
                try:
                    self._ftp.close()
                except Exception:
                    pass
            finally:
                self._ftp = None

    def list_dir(self, remote_path: str) -> List[RemoteEntry]:
        if self._ftp is None:
            raise RuntimeError("FTPS client is not connected.")

        entries: List[RemoteEntry] = []
        try:
            for name, facts in self._ftp.mlsd(remote_path):
                if name in (".", ".."):
                    continue
                entry_type = facts.get("type", "").lower()
                if entry_type in ("cdir", "pdir"):
                    continue
                is_dir = entry_type == "dir"
                size = int(facts.get("size", 0)) if not is_dir else 0
                mtime = _parse_ftp_modify_time(facts.get("modify", ""))
                entries.append(
                    RemoteEntry(
                        name=name,
                        path=posixpath.join(remote_path, name),
                        is_dir=is_dir,
                        size=size,
                        mtime=mtime,
                    )
                )
            return entries
        except ftplib.error_perm:
            # Fallback if server does not support MLSD
            return self._list_dir_fallback(remote_path)

    def _list_dir_fallback(self, remote_path: str) -> List[RemoteEntry]:
        assert self._ftp is not None
        entries: List[RemoteEntry] = []
        current = self._ftp.pwd()
        try:
            names = self._ftp.nlst(remote_path)
        finally:
            self._ftp.cwd(current)

        for raw_name in names:
            name = posixpath.basename(raw_name.rstrip("/"))
            if name in ("", ".", ".."):
                continue
            full_path = posixpath.join(remote_path, name)
            is_dir = False
            try:
                self._ftp.cwd(full_path)
                is_dir = True
                self._ftp.cwd(current)
            except ftplib.error_perm:
                is_dir = False

            size = 0
            mtime = 0.0
            if not is_dir:
                try:
                    size = int(self._ftp.size(full_path) or 0)
                except Exception:
                    size = 0
                try:
                    resp = self._ftp.voidcmd(f"MDTM {full_path}")
                    # Response format: "213 YYYYMMDDHHMMSS"
                    parts = resp.split()
                    if len(parts) >= 2:
                        mtime = _parse_ftp_modify_time(parts[1])
                except Exception:
                    mtime = 0.0

            entries.append(
                RemoteEntry(
                    name=name,
                    path=full_path,
                    is_dir=is_dir,
                    size=size,
                    mtime=mtime,
                )
            )
        return entries

    def stat_file(self, remote_path: str) -> Optional[RemoteEntry]:
        parent = posixpath.dirname(remote_path)
        target_name = posixpath.basename(remote_path)
        for entry in self.list_dir(parent):
            if entry.name == target_name:
                return entry
        return None

    def download_file(self, remote_path: str, local_path: Path) -> None:
        if self._ftp is None:
            raise RuntimeError("FTPS client is not connected.")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as fp:
            self._ftp.retrbinary(f"RETR {remote_path}", fp.write, blocksize=65536)


def create_nas_client(config: NasConfig) -> NasClient:
    """Create the appropriate NAS client based on configuration."""
    proto = config.protocol.lower()
    if proto == "sftp":
        return SftpClient(config)
    if proto == "ftps":
        return FtpsClient(config)
    raise ValueError(f"Unsupported NAS protocol: {config.protocol}")
