import re

from app.ssh import connect_direct


NODE_ORDER = ["dial1", "dial2", "dial3", "dial4"]


def _extension_settings(config):
    settings = config.raw.get("extensions", {})
    start = int(settings.get("start", 161001))
    end = int(settings.get("end", 162800))
    primary_node = settings.get("primary_node", "dial1")
    suffixes = settings.get("node_suffixes", {
        "dial1": "",
        "dial2": "b",
        "dial3": "c",
        "dial4": "d",
    })
    return start, end, primary_node, suffixes


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


def build_extension_plan(config, base_extension):
    base = validate_base_extension(config, base_extension)
    dialers = config.raw.get("dialers", {})
    start, end, primary_node, _ = _extension_settings(config)

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

        nodes.append({
            "node": node_name,
            "hostname_expected": node.get("hostname"),
            "lan_ip": node.get("lan_ip"),
            "wan_ip": node.get("wan_ip"),
            "enabled": enabled,
            "status": "READY" if enabled else "SKIP_DISABLED",
            "primary": node_name == primary_node,
            "extension": variant,
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
        "nodes": nodes,
        "ready_nodes": sum(1 for item in nodes if item.get("status") == "READY"),
        "skipped_nodes": sum(
            1 for item in nodes if item.get("status") == "SKIP_DISABLED"
        ),
        "write_ready": False,
        "block_reason": (
            "Falta definir y validar la plantilla real de phones antes de "
            "habilitar CREATE_PHONE."
        ),
    }


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
        client = connect_direct(node["wan_ip"], config)
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
