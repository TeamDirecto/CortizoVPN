import os
from pathlib import Path

import yaml


class SSHSettings(object):
    def __init__(
        self,
        user,
        port,
        connect_timeout,
        password=None,
        key_file=None,
        target_user=None,
        target_password=None,
        target_key_file=None,
    ):
        self.user = user
        self.port = port
        self.connect_timeout = connect_timeout
        self.password = password
        self.key_file = key_file
        self.target_user = target_user or user
        self.target_password = target_password
        self.target_key_file = target_key_file


class DBSettings(object):
    def __init__(self, name=None, user=None, password=None):
        self.name = name
        self.user = user
        self.password = password


class AppConfig(object):
    def __init__(self, raw, ssh, db):
        self.raw = raw
        self.ssh = ssh
        self.db = db


def load_config(path="config/infrastructure.yml"):
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            "No existe {0}. Copia "
            "config/infrastructure.yml.example a config/infrastructure.yml".format(
                config_path
            )
        )

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    ssh_raw = raw.get("ssh", {})

    ssh = SSHSettings(
        user=os.getenv("CORTIZOVPN_SSH_USER", ssh_raw.get("user", "root")),
        port=int(os.getenv("CORTIZOVPN_SSH_PORT", ssh_raw.get("port", 19600))),
        connect_timeout=int(ssh_raw.get("connect_timeout", 8)),
        password=os.getenv("CORTIZOVPN_SSH_PASSWORD") or None,
        key_file=os.getenv("CORTIZOVPN_SSH_KEY_FILE") or None,
        target_user=os.getenv(
            "CORTIZOVPN_TARGET_SSH_USER",
            ssh_raw.get("target_user") or ssh_raw.get("user", "root"),
        ),
        target_password=os.getenv("CORTIZOVPN_TARGET_SSH_PASSWORD") or None,
        target_key_file=os.getenv("CORTIZOVPN_TARGET_SSH_KEY_FILE") or None,
    )

    db = DBSettings(
        name=os.getenv("CORTIZOVPN_DB_NAME", "asterisk"),
        user=os.getenv("CORTIZOVPN_DB_USER") or None,
        password=os.getenv("CORTIZOVPN_DB_PASSWORD") or None,
    )

    return AppConfig(raw=raw, ssh=ssh, db=db)
