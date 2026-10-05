#!/usr/bin/env python3
import json
import os
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.db import run_readonly_query, run_write_script
from app.services.agent_bulk import (
    build_agent_plan,
    load_group_defaults,
    load_group_templates,
)
from app.services.extensions import _literal, _quote_identifier


CONFIRM_TEXT = "CREATE_332_AGENTS"
CHUNK_SIZE = 20


def _enabled_phone_nodes(config):
    dialers = config.raw.get("dialers", {})
    suffixes = config.raw.get("extensions", {}).get("node_suffixes", {})
    result = []
    for node_name in ("dial1", "dial2", "dial3", "dial4"):
        node = dialers.get(node_name)
        if not node or not node.get("enabled", True):
            continue
        result.append({
            "node": node_name,
            "server_ip": str(node.get("lan_ip")),
            "suffix": str(suffixes.get(node_name, "")),
        })
    return result


def _phone_precheck(config, items):
    enabled = _enabled_phone_nodes(config)
    if not enabled:
        return ["NO_ENABLED_PHONE_NODES"]

    extensions = sorted(set(item["extension"] for item in items))
    server_ips = [node["server_ip"] for node in enabled]

    sql = (
        "SELECT extension, server_ip, login, pass, user_group "
        "FROM phones "
        "WHERE extension IN ({exts}) "
        "AND server_ip IN ({servers})"
    ).format(
        exts=", ".join(_literal(value) for value in extensions),
        servers=", ".join(_literal(value) for value in server_ips),
    )
    rows = run_readonly_query(config, sql, "master")
    found = {}
    for row in rows:
        if len(row) < 5:
            continue
        found[(str(row[0]), str(row[1]))] = {
            "login": str(row[2] or ""),
            "pass": str(row[3] or ""),
            "user_group": str(row[4] or ""),
        }

    problems = []
    target_by_ext = {item["extension"]: item for item in items}
    for extension in extensions:
        target = target_by_ext[extension]
        for node in enabled:
            row = found.get((extension, node["server_ip"]))
            if not row:
                problems.append(
                    "PHONE_MISSING:{0}:{1}".format(extension, node["server_ip"])
                )
                continue

            expected_login = extension + node["suffix"]
            if row["login"] != expected_login:
                problems.append(
                    "PHONE_LOGIN_MISMATCH:{0}:{1}:{2}".format(
                        extension, node["server_ip"], row["login"]
                    )
                )
            if row["pass"] != extension:
                problems.append(
                    "PHONE_PASS_MISMATCH:{0}:{1}".format(
                        extension, node["server_ip"]
                    )
                )
            if row["user_group"] != target["user_group"]:
                problems.append(
                    "PHONE_GROUP_MISMATCH:{0}:{1}:{2}".format(
                        extension, node["server_ip"], row["user_group"]
                    )
                )
    return problems


def _validate_lengths(items):
    problems = []
    for item in items:
        if len(item["user"]) > 20:
            problems.append("USER_TOO_LONG:{0}".format(item["user"]))
        if len(item["full_name"]) > 50:
            problems.append("FULL_NAME_TOO_LONG:{0}".format(item["user"]))
        if len(item["user_group"]) > 20:
            problems.append("GROUP_TOO_LONG:{0}".format(item["user_group"]))
        if len(item["extension"]) > 20:
            problems.append("EXTENSION_TOO_LONG:{0}".format(item["extension"]))
    return problems


def _build_insert(item, defaults, templates, clone_fields):
    group = item["user_group"]
    template = templates[group]

    columns = [
        "user", "pass", "full_name", "user_level",
        "user_group", "active", "phone_login", "phone_pass",
    ] + list(clone_fields)

    select_values = [
        _literal(item["user"]),
        _literal(defaults[group]),
        _literal(item["full_name"]),
        "1",
        _literal(group),
        _literal("N"),
        _literal(item["extension"]),
        _literal(item["extension"]),
    ] + [_quote_identifier(field) for field in clone_fields]

    return (
        "INSERT INTO vicidial_users ({columns}) "
        "SELECT {values} FROM vicidial_users "
        "WHERE user={template} AND user_group={group} LIMIT 1"
    ).format(
        columns=", ".join(_quote_identifier(field) for field in columns),
        values=", ".join(select_values),
        template=_literal(template),
        group=_literal(group),
    )


def _verify_chunk(config, chunk, expected_active):
    users = [item["user"] for item in chunk]
    sql = (
        "SELECT user, full_name, user_group, phone_login, phone_pass, "
        "active, user_level "
        "FROM vicidial_users WHERE user IN ({0}) ORDER BY user"
    ).format(", ".join(_literal(user) for user in users))
    rows = run_readonly_query(config, sql, "master")
    found = {row[0]: row for row in rows}

    problems = []
    for item in chunk:
        row = found.get(item["user"])
        if not row:
            problems.append("USER_NOT_FOUND:{0}".format(item["user"]))
            continue
        checks = [
            ("FULL_NAME", str(row[1] or ""), item["full_name"]),
            ("GROUP", str(row[2] or ""), item["user_group"]),
            ("PHONE_LOGIN", str(row[3] or ""), item["extension"]),
            ("PHONE_PASS", str(row[4] or ""), item["extension"]),
            ("ACTIVE", str(row[5] or ""), expected_active),
            ("LEVEL", str(row[6] or ""), "1"),
        ]
        for label, current, expected in checks:
            if current != expected:
                problems.append(
                    "{0}_MISMATCH:{1}:{2}".format(label, item["user"], current)
                )
    return problems


def _activate_chunk(config, chunk):
    users = [item["user"] for item in chunk]
    sql = (
        "UPDATE vicidial_users SET active='Y' "
        "WHERE active='N' AND user IN ({0})"
    ).format(", ".join(_literal(user) for user in users))
    run_write_script(config, sql + ";", "master")


def _write_manifest(plan):
    data_dir = os.path.join(PROJECT_ROOT, "data")
    if not os.path.isdir(data_dir):
        os.makedirs(data_dir)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(data_dir, "agent_apply_{0}.json".format(stamp))
    payload = {
        "created_at": stamp,
        "source_file": plan["source_file"],
        "target_count": plan["target_count"],
        "users": [
            {
                "user": item["user"],
                "full_name": item["full_name"],
                "user_group": item["user_group"],
                "extension": item["extension"],
            }
            for item in plan["items"]
        ],
    }
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(path, 0o600)
    return path


def main():
    config = load_config()
    plan = build_agent_plan(config)

    print("== AGENT APPLY PRECHECK ==")
    print("target_count : {0}".format(plan["target_count"]))
    print("create_count : {0}".format(plan["create_count"]))
    print("update_count : {0}".format(plan["update_count"]))
    print("correct_count: {0}".format(plan["correct_count"]))
    print("write_ready  : {0}".format(plan["write_ready"]))
    print("blockers     : {0}".format(plan["blockers"]))

    if not plan["write_ready"]:
        print("ABORT: el plan tiene blockers")
        return 2

    if plan["target_count"] != 332:
        print("ABORT: se esperaban exactamente 332 agentes")
        return 2

    if plan["create_count"] != 332 or plan["update_count"] != 0:
        print("ABORT: este APPLY solo acepta el estado inicial CREATE=332 UPDATE=0")
        return 2

    problems = _validate_lengths(plan["items"])
    if problems:
        print("ABORT: errores de longitud")
        for problem in problems[:50]:
            print("  " + problem)
        return 2

    print("Validando phones de las extensiones asignadas...")
    phone_problems = _phone_precheck(config, plan["items"])
    if phone_problems:
        print("ABORT: phones no coincide con el plan de agentes")
        print("phone_problems: {0}".format(len(phone_problems)))
        for problem in phone_problems[:100]:
            print("  " + problem)
        return 2

    print("phones_precheck: OK")

    defaults = load_group_defaults()
    templates = load_group_templates()
    clone_fields = plan["supported_clone_fields"]

    manifest = _write_manifest(plan)
    print("manifest     : {0}".format(manifest))
    print("")
    print("Se crearán 332 usuarios primero active=N,")
    print("se verificará cada bloque y después se activará a Y.")
    answer = input("Escribe {0} para continuar: ".format(CONFIRM_TEXT)).strip()
    if answer != CONFIRM_TEXT:
        print("Cancelado.")
        return 1

    items = [item for item in plan["items"] if item["status"] == "CREATE"]
    created = 0

    try:
        for start in range(0, len(items), CHUNK_SIZE):
            chunk = items[start:start + CHUNK_SIZE]

            statements = [
                _build_insert(item, defaults, templates, clone_fields)
                for item in chunk
            ]
            run_write_script(config, ";\n".join(statements) + ";", "master")

            stage_problems = _verify_chunk(config, chunk, "N")
            if stage_problems:
                print("ABORT: fallo de verificación antes de activar")
                for problem in stage_problems:
                    print("  " + problem)
                print("Los usuarios de este bloque quedan active=N para revisión.")
                return 3

            _activate_chunk(config, chunk)

            active_problems = _verify_chunk(config, chunk, "Y")
            if active_problems:
                print("ABORT: fallo de verificación después de activar")
                for problem in active_problems:
                    print("  " + problem)
                return 3

            created += len(chunk)
            print("Agentes creados y verificados: {0}/332".format(created))

        final_plan = build_agent_plan(config)
        print("")
        print("== FINAL VERIFY ==")
        print("create_count : {0}".format(final_plan["create_count"]))
        print("update_count : {0}".format(final_plan["update_count"]))
        print("correct_count: {0}".format(final_plan["correct_count"]))
        print("blockers     : {0}".format(final_plan["blockers"]))

        if (
            final_plan["create_count"] == 0
            and final_plan["update_count"] == 0
            and final_plan["correct_count"] == 332
            and not final_plan["blockers"]
        ):
            print("APPLY OK: 332/332 agentes correctos")
            return 0

        print("APPLY TERMINO, PERO LA VERIFICACION FINAL NO ES 332/332")
        return 4

    except Exception as exc:
        print("ERROR TYPE: {0}".format(exc.__class__.__name__))
        print("ERROR: {0}".format(str(exc) or repr(exc)))
        traceback.print_exc()
        print("No vuelvas a ejecutar a ciegas; corre scripts/plan_agents.py primero.")
        return 5


if __name__ == "__main__":
    sys.exit(main())
