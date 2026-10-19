#!/usr/bin/env python3
import decimal
import os
import sys
import traceback

import pymysql

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.db import run_readonly_query, run_write_script

ASTGUI_CONF = "/etc/astguiclient.conf"
CONFIRM_TEXT = "COPY_LEVEL8_USERS"
CHUNK_SIZE = 20


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
        read_timeout=30,
        write_timeout=30,
    )


def qident(name):
    tick = chr(96)
    return tick + str(name).replace(tick, tick + tick) + tick


def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return "X'{0}'".format(value.hex())
    raw = str(value).encode("utf-8")
    return "CONVERT(X'{0}' USING utf8mb4)".format(raw.hex())


def target_schema(config):
    rows = run_readonly_query(
        config,
        "SHOW COLUMNS FROM vicidial_users",
        "master",
    )
    columns = []
    auto_increment = set()
    for row in rows:
        if not row:
            continue
        name = row[0]
        columns.append(name)
        extra = str(row[5] or "") if len(row) > 5 else ""
        if "auto_increment" in extra.lower():
            auto_increment.add(name)
    return columns, auto_increment


def source_rows_and_schema():
    src = source_connection()
    try:
        cur = src.cursor()
        cur.execute("SHOW COLUMNS FROM vicidial_users")
        source_cols = [row["Field"] for row in cur.fetchall()]

        cur.execute(
            "SELECT * FROM vicidial_users "
            "WHERE user_level=8 ORDER BY user"
        )
        rows = cur.fetchall()
        return rows, source_cols
    finally:
        src.close()


def existing_target_users(config, users):
    if not users:
        return set()
    quoted = ", ".join(sql_literal(user) for user in users)
    rows = run_readonly_query(
        config,
        "SELECT user FROM vicidial_users "
        "WHERE user IN ({0})".format(quoted),
        "master",
    )
    return set(str(row[0]) for row in rows)


def build_insert(row, common_columns):
    return "INSERT INTO vicidial_users ({0}) VALUES ({1})".format(
        ", ".join(qident(col) for col in common_columns),
        ", ".join(sql_literal(row.get(col)) for col in common_columns),
    )


def verify_target(config, users):
    if not users:
        return set()
    quoted = ", ".join(sql_literal(user) for user in users)
    rows = run_readonly_query(
        config,
        "SELECT user FROM vicidial_users "
        "WHERE user IN ({0})".format(quoted),
        "master",
    )
    return set(str(row[0]) for row in rows)


def main():
    config = load_config()
    source_rows, source_cols = source_rows_and_schema()
    target_cols, auto_increment_cols = target_schema(config)

    common_cols = [
        c for c in source_cols
        if c in target_cols and c not in auto_increment_cols
    ]
    source_only = [c for c in source_cols if c not in target_cols]
    target_only = [c for c in target_cols if c not in source_cols]

    source_users = [str(row["user"]) for row in source_rows]
    existing = existing_target_users(config, source_users)

    create_rows = [
        row for row in source_rows
        if str(row["user"]) not in existing
    ]

    blockers = []
    if "user" not in common_cols:
        blockers.append("USER_COLUMN_NOT_COMMON")
    if "user_level" not in common_cols:
        blockers.append("USER_LEVEL_COLUMN_NOT_COMMON")

    print("== LEVEL 8 USER MIGRATION PRECHECK ==")
    print("source_level8_users : {0}".format(len(source_rows)))
    print("already_existing    : {0}".format(len(existing)))
    print("to_create           : {0}".format(len(create_rows)))
    print("common_columns      : {0}".format(len(common_cols)))
    print("auto_increment_skip : {0}".format(
        ", ".join(sorted(auto_increment_cols)) if auto_increment_cols else "NONE"
    ))
    print("source_only_columns : {0}".format(len(source_only)))
    print("target_only_columns : {0}".format(len(target_only)))
    print("blockers            : {0}".format(blockers))
    print("")

    if existing:
        print("== OMITIDOS POR EXISTIR EN CORTIZO ==")
        for user in sorted(existing):
            print("  {0}".format(user))
        print("")

    print("== A CREAR ==")
    for row in create_rows:
        print(
            "{0:20} level={1} group={2} active={3}".format(
                str(row.get("user") or ""),
                str(row.get("user_level") or ""),
                str(row.get("user_group") or ""),
                str(row.get("active") or ""),
            )
        )

    if source_only:
        print("")
        print("SOURCE-ONLY COLUMNS:")
        print("  " + ", ".join(source_only))

    if target_only:
        print("")
        print("TARGET-ONLY COLUMNS:")
        print("  " + ", ".join(target_only))

    if blockers:
        print("")
        print("ABORT: no se realizaron cambios")
        return 2

    if not create_rows:
        print("")
        print("Nada que crear: todos los usuarios nivel 8 ya existen.")
        return 0

    answer = input(
        "Escribe {0} para copiar solo los faltantes: ".format(CONFIRM_TEXT)
    ).strip()
    if answer != CONFIRM_TEXT:
        print("Cancelado.")
        return 1

    created = 0
    try:
        for start in range(0, len(create_rows), CHUNK_SIZE):
            chunk = create_rows[start:start + CHUNK_SIZE]
            statements = [
                build_insert(row, common_cols)
                for row in chunk
            ]
            run_write_script(
                config,
                ";\n".join(statements) + ";",
                "master",
            )
            created += len(chunk)
            print(
                "Usuarios nivel 8 creados: {0}/{1}".format(
                    created, len(create_rows)
                )
            )

        expected_created = set(str(row["user"]) for row in create_rows)
        found = verify_target(config, list(expected_created))
        missing = sorted(expected_created - found)

        print("")
        print("== FINAL VERIFY ==")
        print("created  : {0}".format(created))
        print("verified : {0}".format(len(found)))
        print("missing  : {0}".format(len(missing)))
        print("omitted  : {0}".format(len(existing)))

        if missing:
            print("MISSING:")
            for user in missing:
                print("  {0}".format(user))
            return 4

        print("APPLY OK: usuarios nivel 8 copiados; existentes omitidos")
        return 0

    except Exception as exc:
        print("ERROR TYPE: {0}".format(exc.__class__.__name__))
        print("ERROR: {0}".format(str(exc) or repr(exc)))
        traceback.print_exc()
        return 5


if __name__ == "__main__":
    sys.exit(main())
