import re
import shlex
import uuid

from app.ssh import connect_via_jump


def _escape_cnf(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n")


def run_readonly_query(config, sql, target_name="master"):
    statement = sql.strip()

    if not re.match(r"^(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN)\b", statement, re.I):
        raise ValueError("Solo se permiten consultas de lectura en esta etapa")

    if not config.db.user or not config.db.password:
        raise RuntimeError(
            "Faltan CORTIZOVPN_DB_USER y/o CORTIZOVPN_DB_PASSWORD"
        )

    databases = config.raw.get("database", {})
    dialers = config.raw.get("dialers", {})

    target = databases.get(target_name)
    if not target:
        raise RuntimeError("Destino de BD desconocido: {0}".format(target_name))

    jump_name = target.get("jump_host")
    jump = dialers.get(jump_name)
    if not jump:
        raise RuntimeError("jump_host desconocido: {0}".format(jump_name))

    if not jump.get("enabled", True):
        raise RuntimeError("jump_host {0} esta deshabilitado".format(jump_name))

    jump_host = jump["wan_ip"]
    target_host = target["host"]
    target_port = int(target.get("port", config.ssh.port))

    jump_client = None
    target_client = None
    remote_cnf = None

    try:
        jump_client, target_client = connect_via_jump(
            jump_host, target_host, target_port, config
        )

        remote_cnf = "/tmp/.cortizovpn-{0}.cnf".format(uuid.uuid4().hex)

        cnf = (
            "[client]\n"
            "user={0}\n"
            "password={1}\n"
            "host=127.0.0.1\n"
        ).format(
            _escape_cnf(config.db.user),
            _escape_cnf(config.db.password),
        )

        sftp = target_client.open_sftp()
        try:
            with sftp.file(remote_cnf, "w") as handle:
                handle.write(cnf)
            sftp.chmod(remote_cnf, 0o600)
        finally:
            sftp.close()

        command = (
            "mysql --defaults-extra-file={cnf} "
            "--batch --raw --skip-column-names "
            "{database} -e {sql}"
        ).format(
            cnf=shlex.quote(remote_cnf),
            database=shlex.quote(config.db.name),
            sql=shlex.quote(statement),
        )

        _, stdout, stderr = target_client.exec_command(command)
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace").strip()
        status = stdout.channel.recv_exit_status()

        if status != 0:
            raise RuntimeError("MariaDB devolvio error: {0}".format(error))

        rows = []
        for line in output.splitlines():
            rows.append(line.split("\t"))

        return rows

    finally:
        if target_client and remote_cnf:
            try:
                target_client.exec_command(
                    "rm -f {0}".format(shlex.quote(remote_cnf))
                )
            except Exception:
                pass

        if target_client:
            target_client.close()
        if jump_client:
            jump_client.close()
