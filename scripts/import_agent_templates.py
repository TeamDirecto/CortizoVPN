#!/usr/bin/env python3
import os
import sys
import traceback

import pymysql

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.db import run_readonly_query, run_write_script
from app.services.agent_bulk import (
    USER_CLONE_FIELDS,
    load_group_defaults,
    load_group_templates,
)
from app.services.extensions import _literal, _quote_identifier


ASTGUI_CONF = "/etc/astguiclient.conf"


def read_astguiclient_conf():
    wanted = {
        "VARDB_server": "host",
        "VARDB_database": "database",
        "VARDB_user": "user",
        "VARDB_pass": "password",
        "VARDB_port": "port",
    }
    values = {}
    with open(ASTGUI_CONF) as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=>" not in line:
                continue
            key, value = [part.strip() for part in line.split("=>", 1)]
            if key in wanted:
                values[wanted[key]] = value
    values["port"] = int(values.get("port") or 3306)
    return values


def source_connection():
    cfg = read_astguiclient_conf()
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=8,
        read_timeout=15,
        write_timeout=15,
    )


def target_schema(config):
    rows = run_readonly_query(config, "SHOW COLUMNS FROM vicidial_users", "master")
    return [row[0] for row in rows if row]


def existing_target_templates(config, templates):
    users = sorted(set(templates.values()))
    if not users:
        return set()
    sql = (
        "SELECT user, user_group FROM vicidial_users "
        "WHERE user IN ({0})"
    ).format(", ".join(_literal(user) for user in users))
    rows = run_readonly_query(config, sql, "master")
    return set((row[0], row[1]) for row in rows if len(row) >= 2)


def fetch_source_template(cursor, user, group, fields):
    columns = ["user", "user_group"] + fields
    sql = (
        "SELECT {0} FROM vicidial_users "
        "WHERE user=%s AND user_group=%s LIMIT 1"
    ).format(", ".join(_quote_identifier(col) for col in columns))
    cursor.execute(sql, (user, group))
    return cursor.fetchone()


def build_insert(group, template_user, password, source, clone_fields):
    identity = {
        "user": template_user,
        "pass": password,
        "full_name": "Usuario Base {0}".format(group),
        "user_level": 1,
        "user_group": group,
        "active": "N",
        "phone_login": "",
        "phone_pass": "",
    }
    columns = [
        "user", "pass", "full_name", "user_level",
        "user_group", "active", "phone_login", "phone_pass",
    ] + clone_fields

    values = []
    for column in columns:
        if column in identity:
            values.append(_literal(identity[column]))
        else:
            values.append(_literal(source.get(column)))

    return "INSERT INTO vicidial_users ({0}) VALUES ({1})".format(
        ", ".join(_quote_identifier(col) for col in columns),
        ", ".join(values),
    )


def main():
    config = load_config()
    templates = load_group_templates()
    defaults = load_group_defaults()
    schema = target_schema(config)
    clone_fields = [field for field in USER_CLONE_FIELDS if field in schema]

    groups = sorted(
        group for group in templates
        if group in defaults and group.startswith("CC-CORTIZO-")
    )
    existing = existing_target_templates(config, templates)

    source = source_connection()
    try:
        cursor = source.cursor()
        plan = []
        missing_source = []
        for group in groups:
            template_user = templates[group]
            if (template_user, group) in existing:
                plan.append((group, template_user, "EXISTS", None))
                continue

            row = fetch_source_template(cursor, template_user, group, clone_fields)
            if not row:
                missing_source.append((group, template_user))
                plan.append((group, template_user, "SOURCE_MISSING", None))
                continue

            sql = build_insert(
                group,
                template_user,
                defaults[group],
                row,
                clone_fields,
            )
            plan.append((group, template_user, "CREATE", sql))
    finally:
        source.close()

    print("== TEMPLATE IMPORT PLAN ==")
    print("groups               : {0}".format(len(groups)))
    print("supported_clone_cols : {0}".format(len(clone_fields)))
    print("target_missing_cols  : {0}".format(len([
        f for f in USER_CLONE_FIELDS if f not in schema
    ])))
    print("create               : {0}".format(sum(1 for x in plan if x[2] == "CREATE")))
    print("exists               : {0}".format(sum(1 for x in plan if x[2] == "EXISTS")))
    print("source_missing       : {0}".format(len(missing_source)))
    print("")

    for group, template_user, status, _ in plan:
        print("{0:26} {1:20} {2}".format(group, template_user, status))

    if missing_source:
        print("")
        print("ABORT: faltan plantillas en la DB fuente de Vici-Users")
        return 2

    create_items = [item for item in plan if item[2] == "CREATE"]
    if not create_items:
        print("Nada que crear.")
        return 0

    answer = input("Escribe IMPORT para crear las plantillas en CortizoVPN: ").strip()
    if answer != "IMPORT":
        print("Cancelado.")
        return 1

    try:
        chunk_size = 10
        created = 0
        for start in range(0, len(create_items), chunk_size):
            chunk = create_items[start:start + chunk_size]
            statements = ["START TRANSACTION"]
            statements.extend(item[3] for item in chunk)
            statements.append("COMMIT")
            run_write_script(config, ";\n".join(statements) + ";", "master")
            created += len(chunk)
            print("Creadas {0}/{1}".format(created, len(create_items)))

        print("IMPORT OK")
        return 0
    except Exception as exc:
        print("ERROR TYPE: {0}".format(exc.__class__.__name__))
        print("ERROR: {0}".format(str(exc) or repr(exc)))
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(main())
