"""Run shell commands on a server over SSH, or on this machine for tests."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .records import RecordError

SSH_EXIT_ERROR = 255


@dataclass
class Completed:
    code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def transport_error(self) -> bool:
        return self.timed_out or self.code == SSH_EXIT_ERROR


class Transport:
    def __init__(self, server: str, server_config: dict[str, Any], ssh_config: dict[str, Any],
                 control_dir: Path):
        self.server = server
        self.kind = server_config.get("transport", "ssh")
        if self.kind not in ("ssh", "local"):
            raise RecordError(f"server {server}: transport must be ssh or local")
        self.ssh_host = server_config.get("ssh_host")
        if self.kind == "ssh" and not self.ssh_host:
            raise RecordError(f"server {server}: ssh transport needs ssh_host")
        self.ssh_config = ssh_config
        self.control_dir = control_dir

    def argv(self, command: str) -> list[str]:
        if self.kind == "local":
            return ["bash", "-c", command]
        self.control_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.control_dir.parent, 0o700)
        os.chmod(self.control_dir, 0o700)
        cfg = self.ssh_config
        return [
            "ssh", "-T", "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={int(cfg.get('connect_timeout_seconds', 15))}",
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={self.control_dir}/%C",
            "-o", f"ControlPersist={int(cfg.get('control_persist_seconds', 120))}",
            "-o", f"ServerAliveInterval={int(cfg.get('server_alive_interval_seconds', 30))}",
            "-o", f"ServerAliveCountMax={int(cfg.get('server_alive_count_max', 3))}",
            self.ssh_host, command,
        ]

    def run(self, command: str, timeout: float = 120, input_text: str | None = None) -> Completed:
        try:
            done = subprocess.run(self.argv(command), input=input_text, capture_output=True, text=True,
                                  timeout=timeout, env=os.environ.copy(),
                                  stdin=None if input_text is not None else subprocess.DEVNULL)
        except subprocess.TimeoutExpired as expired:
            return Completed(124, _text(expired.stdout), _text(expired.stderr), timed_out=True)
        return Completed(done.returncode, done.stdout, done.stderr)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""
