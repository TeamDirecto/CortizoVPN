from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SSHSettings:
    user: str
    port: int
    connect_timeout: int
    password: str | None = None
    key_file: str | None = None


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]
    ssh: SSHSettings


def load_config(path: str | Path = "config/infrastructure.yml") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"No existe {config_path}. Copia "
            "config/infrastructure.yml.example a config/infrastructure.yml"
        )

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    ssh_raw = raw.get("ssh", {})

    ssh = SSHSettings(
        user=os.getenv("CORTIZOVPN_SSH_USER", ssh_raw.get("user", "root")),
        port=int(os.getenv("CORTIZOVPN_SSH_PORT", ssh_raw.get("port", 19600))),
        connect_timeout=int(ssh_raw.get("connect_timeout", 8)),
        password=os.getenv("CORTIZOVPN_SSH_PASSWORD") or None,
        key_file=os.getenv("CORTIZOVPN_SSH_KEY_FILE") or None,
    )

    return AppConfig(raw=raw, ssh=ssh)
