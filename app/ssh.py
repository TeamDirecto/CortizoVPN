from __future__ import annotations

from dataclasses import dataclass

import paramiko

from app.config import AppConfig


@dataclass
class SSHResult:
    ok: bool
    detail: str


def _new_client() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return client


def connect_direct(host: str, config: AppConfig) -> paramiko.SSHClient:
    client = _new_client()
    kwargs = {
        "hostname": host,
        "username": config.ssh.user,
        "port": config.ssh.port,
        "timeout": config.ssh.connect_timeout,
        "banner_timeout": config.ssh.connect_timeout,
        "auth_timeout": config.ssh.connect_timeout,
        "allow_agent": True,
        "look_for_keys": True,
    }

    if config.ssh.password:
        kwargs["password"] = config.ssh.password
    if config.ssh.key_file:
        kwargs["key_filename"] = config.ssh.key_file

    client.connect(**kwargs)
    return client


def test_direct(host: str, config: AppConfig) -> SSHResult:
    client = None
    try:
        client = connect_direct(host, config)
        _, stdout, _ = client.exec_command("hostname")
        hostname = stdout.read().decode().strip() or host
        return SSHResult(True, hostname)
    except Exception as exc:
        return SSHResult(False, str(exc))
    finally:
        if client:
            client.close()


def connect_via_jump(
    jump_host: str,
    target_host: str,
    target_port: int,
    config: AppConfig,
) -> tuple[paramiko.SSHClient, paramiko.SSHClient]:
    jump_client = connect_direct(jump_host, config)
    jump_transport = jump_client.get_transport()
    if jump_transport is None:
        jump_client.close()
        raise RuntimeError("No se pudo obtener el transporte SSH del jump host")

    channel = jump_transport.open_channel(
        "direct-tcpip",
        (target_host, target_port),
        ("127.0.0.1", 0),
    )

    target_client = _new_client()
    kwargs = {
        "hostname": target_host,
        "username": config.ssh.user,
        "port": target_port,
        "sock": channel,
        "timeout": config.ssh.connect_timeout,
        "banner_timeout": config.ssh.connect_timeout,
        "auth_timeout": config.ssh.connect_timeout,
        "allow_agent": True,
        "look_for_keys": True,
    }

    if config.ssh.password:
        kwargs["password"] = config.ssh.password
    if config.ssh.key_file:
        kwargs["key_filename"] = config.ssh.key_file

    try:
        target_client.connect(**kwargs)
    except Exception:
        target_client.close()
        jump_client.close()
        raise

    return jump_client, target_client


def test_via_jump(
    jump_host: str,
    target_host: str,
    target_port: int,
    config: AppConfig,
) -> SSHResult:
    jump_client = None
    target_client = None
    try:
        jump_client, target_client = connect_via_jump(
            jump_host, target_host, target_port, config
        )
        _, stdout, _ = target_client.exec_command("hostname")
        hostname = stdout.read().decode().strip() or target_host
        return SSHResult(True, hostname)
    except Exception as exc:
        return SSHResult(False, str(exc))
    finally:
        if target_client:
            target_client.close()
        if jump_client:
            jump_client.close()
