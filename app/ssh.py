import os

import paramiko


class SSHResult(object):
    def __init__(self, ok, detail):
        self.ok = ok
        self.detail = detail


def _new_client():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return client


def node_ssh_options(node, config):
    node = node or {}
    user = node.get("ssh_user") or config.ssh.user
    password_env = node.get("ssh_password_env")
    key_file_env = node.get("ssh_key_file_env")
    password = os.getenv(password_env) if password_env else config.ssh.password
    key_file = os.getenv(key_file_env) if key_file_env else config.ssh.key_file
    return user, password, key_file


def connect_direct(host, config, username=None, password=None, key_file=None):
    client = _new_client()
    kwargs = {
        "hostname": host,
        "username": username or config.ssh.user,
        "port": config.ssh.port,
        "timeout": config.ssh.connect_timeout,
        "banner_timeout": config.ssh.connect_timeout,
        "auth_timeout": config.ssh.connect_timeout,
        "allow_agent": True,
        "look_for_keys": True,
    }

    effective_password = password if password is not None else config.ssh.password
    effective_key_file = key_file if key_file is not None else config.ssh.key_file
    if effective_password:
        kwargs["password"] = effective_password
    if effective_key_file:
        kwargs["key_filename"] = effective_key_file

    client.connect(**kwargs)
    return client


def test_direct(host, config, username=None, password=None, key_file=None):
    client = None
    try:
        client = connect_direct(
            host, config, username=username, password=password, key_file=key_file
        )
        _, stdout, _ = client.exec_command("hostname")
        hostname = stdout.read().decode().strip() or host
        return SSHResult(True, hostname)
    except Exception as exc:
        return SSHResult(False, str(exc))
    finally:
        if client:
            client.close()


def connect_via_jump(
    jump_host,
    target_host,
    target_port,
    config,
    jump_user=None,
    jump_password=None,
    jump_key_file=None,
    target_user=None,
    target_password=None,
    target_key_file=None,
):
    jump_client = connect_direct(
        jump_host,
        config,
        username=jump_user,
        password=jump_password,
        key_file=jump_key_file,
    )
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
        "username": target_user or config.ssh.target_user,
        "port": target_port,
        "sock": channel,
        "timeout": config.ssh.connect_timeout,
        "banner_timeout": config.ssh.connect_timeout,
        "auth_timeout": config.ssh.connect_timeout,
        "allow_agent": True,
        "look_for_keys": True,
    }

    effective_target_password = (
        target_password
        if target_password is not None
        else config.ssh.target_password
    )
    effective_target_key_file = (
        target_key_file
        if target_key_file is not None
        else config.ssh.target_key_file
    )
    if effective_target_password:
        kwargs["password"] = effective_target_password
    if effective_target_key_file:
        kwargs["key_filename"] = effective_target_key_file

    try:
        target_client.connect(**kwargs)
    except Exception:
        target_client.close()
        jump_client.close()
        raise

    return jump_client, target_client


def test_via_jump(
    jump_host,
    target_host,
    target_port,
    config,
    jump_user=None,
    jump_password=None,
    jump_key_file=None,
    target_user=None,
    target_password=None,
    target_key_file=None,
):
    jump_client = None
    target_client = None
    try:
        jump_client, target_client = connect_via_jump(
            jump_host,
            target_host,
            target_port,
            config,
            jump_user=jump_user,
            jump_password=jump_password,
            jump_key_file=jump_key_file,
            target_user=target_user,
            target_password=target_password,
            target_key_file=target_key_file,
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
