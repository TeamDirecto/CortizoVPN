import re
import time

from app.db import run_readonly_query, run_write_script
from app.ssh import connect_direct, node_ssh_options


NODE_ORDER = ["dial1", "dial2", "dial3", "dial4"]
TEMPLATE_EXTENSION = "161001"


def _extension_settings(config):
    settings = config.raw.get("extensions", {})
    raw_start = settings.get("start")
    raw_end = settings.get("end")
    start = int(raw_start) if raw_start not in (None, "") else 161001
    end = int(raw_end) if raw_end not in (None, "") else 162800
    primary_node = settings.get("primary_node", "dial1")
    suffixes = settings.get("node_suffixes", {
        "dial1": "",
        "dial2": "b",
        "dial3": "c",
        "dial4": "d",
    })
    return start, end, primary_node, suffixes


def _sql_quote(value):
    return str(value).replace("'", "''")


def _literal(value):
    if value is None:
        return "NULL"
    return "'{0}'".format(_sql_quote(value))


def _quote_identifier(value):
    return "`{0}`".format(str(value).replace("`", "``"))


def validate_base_extension(config, extension):
    value = str(extension).strip()
    if not re.match(r"^\d{6}$", value):
        raise ValueError("La extension base debe tener 6 digitos")

    number = int(value)
    start, end, _, _ = _extension_settings(config)
    if number < start or number > end:
        raise ValueError(
            "Extension fuera de rango Cortizo: {0}-{1}".format(start, end)
        )
    return value


def extension_for_node(config, base_extension, node_name):
    base = validate_base_extension(config, base_extension)
    _, _, _, suffixes = _extension_settings(config)
    if node_name not in suffixes:
        raise ValueError("No hay sufijo configurado para {0}".format(node_name))
    return "{0}{1}".format(base, suffixes[node_name])


def _phones_columns(config):
    rows = run_readonly_query(config, "SHOW COLUMNS FROM phones", "master")
    return [row[0] for row in rows if row]


def _existing_phone_map(config, base_extension):
    variants = [
        extension_for_node(config, base_extension, node)
        for node in NODE_ORDER
    ]
    sql = (
        "SELECT extension, server_ip FROM phones "
        "WHERE extension IN ({0})"
    ).format(", ".join(_literal(v) for v in variants))
    rows = run_readonly_query(config, sql, "master")
    return {row[0]: row[1] for row in rows if len(row) >= 2}


def build_extension_plan(config, base_extension):
    base = validate_base_extension(config, base_extension)
    dialers = config.raw.get("dialers", {})
    start, end, primary_node, _ = _extension_settings(config)
    existing = _existing_phone_map(config, base)

    nodes = []
    for node_name in NODE_ORDER:
        node = dialers.get(node_name)
        if not node:
            nodes.append({
                "node": node_name,
                "status": "CONFIG_MISSING",
                "enabled": False,
            })
            continue

        enabled = bool(node.get("enabled", True))
        variant = extension_for_node(config, base, node_name)
        exists_server = existing.get(variant)

        if not enabled:
            status = "SKIP_DISABLED"
        elif exists_server == node.get("lan_ip"):
            status = "EXISTS"
        elif exists_server:
            status = "CONFLICT"
        else:
            status = "CREATE"

        nodes.append({
            "node": node_name,
            "hostname_expected": node.get("hostname"),
            "lan_ip": node.get("lan_ip"),
            "wan_ip": node.get("wan_ip"),
            "enabled": enabled,
            "status": status,
            "primary": node_name == primary_node,
            "extension": variant,
            "existing_server_ip": exists_server,
            "steps": [
                "CREATE_PHONE",
                "REQUEST_REBUILD_CONF",
                "WAIT_REBUILD",
                "SIP_RELOAD",
                "VERIFY_SIP_PEER",
            ],
        })

    return {
        "base_extension": base,
        "range_start": start,
        "range_end": end,
        "primary_node": primary_node,
        "template_extension": TEMPLATE_EXTENSION,
        "nodes": nodes,
        "create_count": sum(1 for item in nodes if item.get("status") == "CREATE"),
        "exists_count": sum(1 for item in nodes if item.get("status") == "EXISTS"),
        "conflict_count": sum(1 for item in nodes if item.get("status") == "CONFLICT"),
        "skipped_nodes": sum(
            1 for item in nodes if item.get("status") == "SKIP_DISABLED"
        ),
        "write_ready": all(
            item.get("status") in ("CREATE", "EXISTS", "SKIP_DISABLED")
            for item in nodes
        ),
    }


def _build_phone_insert(config, target_extension, server_ip):
    columns = _phones_columns(config)
    if not columns:
        raise RuntimeError("No se pudieron detectar columnas de phones")

    select_values = []
    for column in columns:
        if column in ("extension", "dialplan_number", "voicemail_id",
                      "login", "pass", "fullname", "outbound_cid"):
            select_values.append(_literal(target_extension))
        elif column == "server_ip":
            select_values.append(_literal(server_ip))
        elif column in ("phone_ip", "computer_ip"):
            select_values.append("''")
        elif column == "peer_status":
            select_values.append(_literal("UNREGISTERED"))
        elif column == "ping_time":
            select_values.append("0")
        else:
            select_values.append(_quote_identifier(column))

    return (
        "INSERT INTO phones ({columns}) "
        "SELECT {values} FROM phones "
        "WHERE extension={template} LIMIT 1"
    ).format(
        columns=", ".join(_quote_identifier(c) for c in columns),
        values=", ".join(select_values),
        template=_literal(TEMPLATE_EXTENSION),
    )


def _request_rebuild(config, server_ips):
    if not server_ips:
        return
    sql = (
        "UPDATE servers SET rebuild_conf_files='Y' "
        "WHERE server_ip IN ({0})"
    ).format(", ".join(_literal(ip) for ip in server_ips))
    run_write_script(config, sql + ";", "master")


def _wait_rebuild(config, server_ips, timeout=45, interval=2):
    if not server_ips:
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        sql = (
            "SELECT server_ip, rebuild_conf_files FROM servers "
            "WHERE server_ip IN ({0}) ORDER BY server_ip"
        ).format(", ".join(_literal(ip) for ip in server_ips))
        rows = run_readonly_query(config, sql, "master")
        states = {row[0]: row[1] for row in rows if len(row) >= 2}
        if states and all(states.get(ip) == "N" for ip in server_ips):
            return True
        time.sleep(interval)
    return False


def _sip_reload(config, node_name):
    node = config.raw.get("dialers", {}).get(node_name)
    if not node or not node.get("enabled", True):
        return {"node": node_name, "status": "SKIP_DISABLED"}

    client = None
    try:
        node_user, node_password, node_key_file = node_ssh_options(node, config)
        client = connect_direct(
            node["wan_ip"],
            config,
            username=node_user,
            password=node_password,
            key_file=node_key_file,
        )
        _, stdout, stderr = client.exec_command('asterisk -rx "sip reload"')
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace").strip()
        status = stdout.channel.recv_exit_status()
        return {
            "node": node_name,
            "status": "OK" if status == 0 else "ERROR",
            "detail": output.strip() or error,
        }
    finally:
        if client:
            client.close()


def verify_sip_peer(config, node_name, base_extension):
    dialers = config.raw.get("dialers", {})
    node = dialers.get(node_name)
    if not node:
        raise RuntimeError("Dialer desconocido: {0}".format(node_name))
    if not node.get("enabled", True):
        return {
            "node": node_name,
            "status": "SKIP_DISABLED",
            "extension": extension_for_node(config, base_extension, node_name),
        }

    extension = extension_for_node(config, base_extension, node_name)
    client = None
    try:
        node_user, node_password, node_key_file = node_ssh_options(node, config)
        client = connect_direct(
            node["wan_ip"],
            config,
            username=node_user,
            password=node_password,
            key_file=node_key_file,
        )
        command = 'asterisk -rx "sip show peer {0}"'.format(extension)
        _, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace").strip()
        status = stdout.channel.recv_exit_status()

        exists = status == 0 and (
            "Name" in output or
            "Secret" in output or
            extension in output
        )

        return {
            "node": node_name,
            "extension": extension,
            "status": "OK" if exists else "NOT_FOUND",
            "command_status": status,
            "detail": output.strip() or error,
        }
    finally:
        if client:
            client.close()


def verify_extension_cluster(config, base_extension):
    plan = build_extension_plan(config, base_extension)
    results = []
    for item in plan["nodes"]:
        if item.get("status") == "CONFIG_MISSING":
            results.append(item)
            continue
        results.append(
            verify_sip_peer(config, item["node"], base_extension)
        )

    return {
        "base_extension": plan["base_extension"],
        "results": results,
        "ok": all(
            item.get("status") in ("OK", "SKIP_DISABLED")
            for item in results
        ),
    }


def apply_extension(config, base_extension):
    plan = build_extension_plan(config, base_extension)

    if plan["conflict_count"]:
        raise RuntimeError(
            "Hay conflictos de extension; no se aplicaron cambios"
        )

    create_items = [
        item for item in plan["nodes"]
        if item.get("status") == "CREATE"
    ]

    if create_items:
        statements = ["START TRANSACTION"]
        for item in create_items:
            statements.append(
                _build_phone_insert(
                    config,
                    item["extension"],
                    item["lan_ip"],
                )
            )
        statements.append("COMMIT")
        run_write_script(config, ";\n".join(statements) + ";", "master")

    rebuild_ips = [item["lan_ip"] for item in create_items]
    _request_rebuild(config, rebuild_ips)
    rebuild_ok = _wait_rebuild(config, rebuild_ips)

    reload_results = []
    verify_results = []

    for item in plan["nodes"]:
        if item.get("status") == "SKIP_DISABLED":
            reload_results.append({
                "node": item["node"],
                "status": "SKIP_DISABLED",
            })
            verify_results.append({
                "node": item["node"],
                "extension": item["extension"],
                "status": "SKIP_DISABLED",
            })
            continue

        if item.get("status") in ("CREATE", "EXISTS"):
            reload_results.append(_sip_reload(config, item["node"]))
            verify_results.append(
                verify_sip_peer(config, item["node"], base_extension)
            )

    after = build_extension_plan(config, base_extension)

    return {
        "base_extension": base_extension,
        "created": len(create_items),
        "created_extensions": [
            item["extension"] for item in create_items
        ],
        "rebuild_ok": rebuild_ok,
        "reload": reload_results,
        "verify": verify_results,
        "plan_after": after,
        "ok": (
            rebuild_ok and
            all(
                item.get("status") in ("OK", "SKIP_DISABLED")
                for item in reload_results
            ) and
            all(
                item.get("status") in ("OK", "SKIP_DISABLED")
                for item in verify_results
            )
        ),
    }
