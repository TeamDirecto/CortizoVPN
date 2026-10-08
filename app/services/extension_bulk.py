import re

from app.db import run_readonly_query, run_write_script
from app.ssh import connect_direct, node_ssh_options
from app.services.extensions import (
    NODE_ORDER,
    TEMPLATE_EXTENSION,
    _extension_settings,
    _phones_columns,
    _literal,
    _quote_identifier,
    _request_rebuild,
    _wait_rebuild,
    _sip_reload,
    extension_for_node,
)


BLOCKS = [
    ("CC-CORTIZO-BANCO-AZT", 161001, 28),
    ("CC-CORTIZO-BANORTE", 161101, 21),
    ("CC-CORTIZO-BBVA", 161201, 14),
    ("CC-CORTIZO-GMF", 161301, 25),
    ("CC-CORTIZO-LABORATOR", 161401, 0),
    ("CC-CORTIZO-LIBERTAD", 161501, 16),
    ("CC-CORTIZO-NISSAN", 161601, 26),
    ("CC-CORTIZO-NISSAN-LA", 161701, 25),
    ("CC-CORTIZO-RAPIDAUTO", 161801, 6),
    ("CC-CORTIZO-SA", 161901, 18),
    ("CC-CORTIZO-SCOTI", 162001, 8),
    ("CC-CORTIZO-SCOTI-AUT", 162101, 11),
    ("CC-CORTIZO-SCOTI-PR", 162201, 3),
    ("CC-CORTIZO-SICREA", 162301, 11),
    ("CC-CORTIZO-TOTALPLAY", 162401, 66),
    ("CC-CORTIZO-TOYOTA", 162501, 13),
    ("CC-CORTIZO-UNIFIN", 162601, 4),
    ("CC-CORTIZO-VW", 162701, 37),
]


def target_rows():
    rows = []
    for user_group, start, count in BLOCKS:
        for offset in range(count):
            rows.append({
                "user_group": user_group,
                "base_extension": str(start + offset),
            })
    return rows


def phone_extension_for_node(base_extension, node_name):
    return str(base_extension)


def phone_login_for_node(config, base_extension, node_name):
    return extension_for_node(config, base_extension, node_name)


def dialplan_for_node(base_extension, node_name):
    base = str(base_extension)
    prefixes = {
        "dial1": "",
        "dial2": "1",
        "dial3": "2",
        "dial4": "3",
    }
    return "{0}{1}".format(prefixes[node_name], base)


def _existing_phones(config):
    rows = run_readonly_query(
        config,
        "SELECT extension, server_ip, dialplan_number, voicemail_id, login, "
        "pass, fullname, outbound_cid, user_group FROM phones "
        "WHERE extension LIKE '161%' OR extension LIKE '162%'",
        "master",
    )
    result = {}
    for row in rows:
        if len(row) < 2:
            continue
        result[(row[0], row[1])] = {
            "extension": row[0],
            "server_ip": row[1],
            "dialplan_number": row[2] if len(row) > 2 else "",
            "voicemail_id": row[3] if len(row) > 3 else "",
            "login": row[4] if len(row) > 4 else "",
            "pass": row[5] if len(row) > 5 else "",
            "fullname": row[6] if len(row) > 6 else "",
            "outbound_cid": row[7] if len(row) > 7 else "",
            "user_group": row[8] if len(row) > 8 else "",
        }
    return result


def build_bulk_plan(config):
    dialers = config.raw.get("dialers", {})
    existing = _existing_phones(config)
    targets = target_rows()
    items = []
    conflicts = []
    create_count = 0
    exists_count = 0
    update_count = 0
    skipped_count = 0

    for row in targets:
        base = row["base_extension"]
        for node_name in NODE_ORDER:
            node = dialers.get(node_name)
            if not node:
                items.append({
                    "user_group": row["user_group"],
                    "base_extension": base,
                    "node": node_name,
                    "status": "CONFIG_MISSING",
                })
                continue

            server_ip = node.get("lan_ip")
            phone_extension = phone_extension_for_node(base, node_name)
            login = phone_login_for_node(config, base, node_name)
            dialplan_number = dialplan_for_node(base, node_name)
            desired = {
                "extension": phone_extension,
                "server_ip": server_ip,
                "dialplan_number": dialplan_number,
                "voicemail_id": base,
                "login": login,
                "pass": base,
                "fullname": "ext {0}".format(base),
                "outbound_cid": "0000000000",
                "user_group": row["user_group"],
            }

            current = existing.get((phone_extension, server_ip))
            legacy_variant = existing.get((login, server_ip)) if login != phone_extension else None

            if not node.get("enabled", True):
                status = "SKIP_DISABLED"
                skipped_count += 1
            elif current:
                mismatched = any(
                    str(current.get(key) or "") != str(value or "")
                    for key, value in desired.items()
                    if key not in ("extension", "server_ip")
                )
                if legacy_variant:
                    status = "MERGE_LEGACY_VARIANT"
                    update_count += 1
                elif mismatched:
                    status = "UPDATE_METADATA"
                    update_count += 1
                else:
                    status = "EXISTS"
                    exists_count += 1
            elif legacy_variant:
                status = "REKEY_LEGACY_VARIANT"
                update_count += 1
            else:
                status = "CREATE"
                create_count += 1

            items.append({
                "user_group": row["user_group"],
                "base_extension": base,
                "node": node_name,
                "extension": phone_extension,
                "login": login,
                "dialplan_number": dialplan_number,
                "server_ip": server_ip,
                "status": status,
                "desired": desired,
            })

    groups = []
    for user_group, start, count in BLOCKS:
        groups.append({
            "user_group": user_group,
            "range_start": start,
            "range_end": start + 99,
            "assigned": count,
        })

    return {
        "base_count": len(targets),
        "phone_target_count": len(targets) * len(NODE_ORDER),
        "create_count": create_count,
        "exists_count": exists_count,
        "update_count": update_count,
        "skipped_count": skipped_count,
        "conflict_count": len(conflicts),
        "write_ready": len(conflicts) == 0,
        "groups": groups,
        "conflicts": conflicts,
        "items": items,
    }


def _build_insert_with_columns(columns, item):
    desired = item["desired"]
    values = []
    for column in columns:
        if column in desired:
            values.append(_literal(desired[column]))
        elif column in ("phone_ip", "computer_ip"):
            values.append("''")
        elif column == "peer_status":
            values.append(_literal("UNREGISTERED"))
        elif column == "ping_time":
            values.append("0")
        else:
            values.append(_quote_identifier(column))

    return (
        "INSERT INTO phones ({columns}) "
        "SELECT {values} FROM phones "
        "WHERE extension={template} AND server_ip='10.10.15.11' LIMIT 1"
    ).format(
        columns=", ".join(_quote_identifier(col) for col in columns),
        values=", ".join(values),
        template=_literal(TEMPLATE_EXTENSION),
    )


def _run_insert_chunks(config, create_items, chunk_size=10):
    columns = _phones_columns(config)
    if not columns:
        raise RuntimeError("No se pudieron detectar columnas de phones")

    created = 0
    for start in range(0, len(create_items), chunk_size):
        chunk = create_items[start:start + chunk_size]
        statements = ["START TRANSACTION"]
        for item in chunk:
            statements.append(
                _build_insert_with_columns(columns, item)
            )
        statements.append("COMMIT")
        run_write_script(config, ";\n".join(statements) + ";", "master")
        created += len(chunk)
    return created


def _update_existing_metadata(config, update_items, chunk_size=25):
    if not update_items:
        return 0

    updated = 0
    for start in range(0, len(update_items), chunk_size):
        chunk = update_items[start:start + chunk_size]
        statements = ["START TRANSACTION"]
        for item in chunk:
            if item["status"] != "UPDATE_METADATA":
                continue
            desired = item["desired"]
            statements.append(
                "UPDATE phones SET "
                "dialplan_number={dialplan_number}, voicemail_id={voicemail_id}, "
                "login={login}, pass={password}, fullname={fullname}, "
                "outbound_cid={outbound_cid}, user_group={user_group} "
                "WHERE extension={extension} AND server_ip={server_ip}".format(
                    dialplan_number=_literal(desired["dialplan_number"]),
                    voicemail_id=_literal(desired["voicemail_id"]),
                    login=_literal(desired["login"]),
                    password=_literal(desired["pass"]),
                    fullname=_literal(desired["fullname"]),
                    outbound_cid=_literal(desired["outbound_cid"]),
                    user_group=_literal(desired["user_group"]),
                    extension=_literal(desired["extension"]),
                    server_ip=_literal(desired["server_ip"]),
                )
            )
        if len(statements) == 1:
            continue
        statements.append("COMMIT")
        run_write_script(config, ";\n".join(statements) + ";", "master")
        updated += len([i for i in chunk if i["status"] == "UPDATE_METADATA"])

    return updated


def _verify_node_peers(config, node_name, expected):
    node = config.raw.get("dialers", {}).get(node_name)
    if not node or not node.get("enabled", True):
        return {
            "node": node_name,
            "status": "SKIP_DISABLED",
            "expected": len(expected),
            "found": 0,
            "missing": [],
        }

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
        _, stdout, stderr = client.exec_command('asterisk -rx "sip show peers"')
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace").strip()
        status = stdout.channel.recv_exit_status()
        if status != 0:
            return {
                "node": node_name,
                "status": "ERROR",
                "expected": len(expected),
                "found": 0,
                "missing": expected[:20],
                "detail": error,
            }

        lines = output.splitlines()
        found = []
        missing = []
        for extension in expected:
            pattern = re.compile(r"^" + re.escape(extension) + r"(?:/|\s)")
            if any(pattern.search(line) for line in lines):
                found.append(extension)
            else:
                missing.append(extension)

        return {
            "node": node_name,
            "status": "OK" if not missing else "MISSING",
            "expected": len(expected),
            "found": len(found),
            "missing": missing[:20],
            "missing_count": len(missing),
        }
    finally:
        if client:
            client.close()


def apply_bulk(config):
    plan = build_bulk_plan(config)
    if plan["conflict_count"]:
        raise RuntimeError(
            "Hay {0} conflictos; no se aplicaron cambios".format(
                plan["conflict_count"]
            )
        )

    create_items = [
        item for item in plan["items"]
        if item["status"] == "CREATE"
    ]
    structural_items = [
        item for item in plan["items"]
        if item["status"] in ("REKEY_LEGACY_VARIANT", "MERGE_LEGACY_VARIANT")
    ]
    if structural_items:
        raise RuntimeError(
            "Se detectaron {0} phones con estructura anterior; "
            "ejecuta scripts/repair_phone_structure.py antes del bulk apply".format(
                len(structural_items)
            )
        )

    update_items = [
        item for item in plan["items"]
        if item["status"] == "UPDATE_METADATA"
    ]

    updated = _update_existing_metadata(config, update_items)
    created = _run_insert_chunks(config, create_items) if create_items else 0

    rebuild_ips = sorted(set(
        item["server_ip"] for item in create_items if item.get("server_ip")
    ))
    _request_rebuild(config, rebuild_ips)
    rebuild_ok = _wait_rebuild(config, rebuild_ips, timeout=120, interval=2)

    reload_results = []
    verify_results = []
    targets = target_rows()

    for node_name in NODE_ORDER:
        node = config.raw.get("dialers", {}).get(node_name)
        if not node or not node.get("enabled", True):
            reload_results.append({
                "node": node_name,
                "status": "SKIP_DISABLED",
            })
            verify_results.append({
                "node": node_name,
                "status": "SKIP_DISABLED",
                "expected": len(targets),
                "found": 0,
                "missing": [],
            })
            continue

        reload_results.append(_sip_reload(config, node_name))
        expected = [
            phone_login_for_node(config, row["base_extension"], node_name)
            for row in targets
        ]
        verify_results.append(
            _verify_node_peers(config, node_name, expected)
        )

    after = build_bulk_plan(config)

    return {
        "base_count": len(targets),
        "created": created,
        "updated_metadata": updated,
        "rebuild_ok": rebuild_ok,
        "reload": reload_results,
        "verify": verify_results,
        "plan_after": {
            "create_count": after["create_count"],
            "exists_count": after["exists_count"],
            "update_count": after["update_count"],
            "skipped_count": after["skipped_count"],
            "conflict_count": after["conflict_count"],
            "write_ready": after["write_ready"],
        },
        "ok": (
            rebuild_ok
            and after["create_count"] == 0
            and after["update_count"] == 0
            and after["conflict_count"] == 0
            and all(
                item.get("status") in ("OK", "SKIP_DISABLED")
                for item in reload_results
            )
            and all(
                item.get("status") in ("OK", "SKIP_DISABLED")
                for item in verify_results
            )
        ),
    }
