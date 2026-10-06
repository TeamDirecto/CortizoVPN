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
from app.services.user_groups import TARGET_GROUPS

ASTGUI_CONF = "/etc/astguiclient.conf"
CONFIRM_TEXT = "COPY_169_PAUSE_CODES"
CHUNK_SIZE = 25


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


def parse_allowed_campaigns(raw):
    result = []
    seen = set()
    for token in str(raw or "").split():
        token = token.strip()
        if not token or token == "-":
            continue
        if token not in seen:
            result.append(token)
            seen.add(token)
    return result


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


def source_payload():
    groups = [group for group, _ in TARGET_GROUPS]
    src = source_connection()
    try:
        cur = src.cursor()

        marks = ",".join(["%s"] * len(groups))
        cur.execute(
            "SELECT user_group, allowed_campaigns "
            "FROM vicidial_user_groups "
            "WHERE user_group IN ({0})".format(marks),
            tuple(groups),
        )

        campaign_ids = []
        seen = set()
        for row in cur.fetchall():
            for campaign_id in parse_allowed_campaigns(row.get("allowed_campaigns")):
                if campaign_id not in seen:
                    campaign_ids.append(campaign_id)
                    seen.add(campaign_id)

        marks = ",".join(["%s"] * len(campaign_ids))
        cur.execute(
            "SELECT * FROM vicidial_pause_codes "
            "WHERE campaign_id IN ({0}) "
            "ORDER BY campaign_id, pause_code".format(marks),
            tuple(campaign_ids),
        )
        rows = cur.fetchall()

        cur.execute("SHOW COLUMNS FROM vicidial_pause_codes")
        columns = [row["Field"] for row in cur.fetchall()]

        return campaign_ids, rows, columns
    finally:
        src.close()


def target_columns(config):
    rows = run_readonly_query(
        config,
        "SHOW COLUMNS FROM vicidial_pause_codes",
        "master",
    )
    return [row[0] for row in rows]


def existing_target_rows(config, campaign_ids):
    ids = ", ".join(sql_literal(cid) for cid in campaign_ids)
    rows = run_readonly_query(
        config,
        "SELECT campaign_id, pause_code "
        "FROM vicidial_pause_codes "
        "WHERE campaign_id IN ({0}) "
        "ORDER BY campaign_id, pause_code".format(ids),
        "master",
    )
    return [(str(row[0]), str(row[1])) for row in rows]


def campaign_count(config, campaign_ids):
    ids = ", ".join(sql_literal(cid) for cid in campaign_ids)
    rows = run_readonly_query(
        config,
        "SELECT campaign_id FROM vicidial_campaigns "
        "WHERE campaign_id IN ({0})".format(ids),
        "master",
    )
    return len(rows)


def build_insert(row, columns):
    return "INSERT INTO vicidial_pause_codes ({0}) VALUES ({1})".format(
        ", ".join(qident(column) for column in columns),
        ", ".join(sql_literal(row.get(column)) for column in columns),
    )


def verify(config, campaign_ids):
    ids = ", ".join(sql_literal(cid) for cid in campaign_ids)
    rows = run_readonly_query(
        config,
        "SELECT campaign_id, pause_code "
        "FROM vicidial_pause_codes "
        "WHERE campaign_id IN ({0})".format(ids),
        "master",
    )
    return set((str(row[0]), str(row[1])) for row in rows)


def main():
    config = load_config()

    campaign_ids, source_rows, source_cols = source_payload()
    target_cols = target_columns(config)
    common_cols = [c for c in source_cols if c in target_cols]
    source_only = [c for c in source_cols if c not in target_cols]
    target_only = [c for c in target_cols if c not in source_cols]

    existing = existing_target_rows(config, campaign_ids)
    campaigns_present = campaign_count(config, campaign_ids)

    expected_pairs = set(
        (str(row["campaign_id"]), str(row["pause_code"]))
        for row in source_rows
    )

    blockers = []
    if len(campaign_ids) != 63:
        blockers.append("UNEXPECTED_CAMPAIGN_COUNT")
    if len(source_rows) != 169:
        blockers.append("UNEXPECTED_PAUSE_CODE_COUNT")
    if campaigns_present != 63:
        blockers.append("TARGET_CAMPAIGNS_INCOMPLETE")
    if existing:
        blockers.append("TARGET_PAUSE_CODES_ALREADY_EXIST")
    if "campaign_id" not in common_cols or "pause_code" not in common_cols:
        blockers.append("PAUSE_SCHEMA_KEY_MISMATCH")
    if source_only or target_only:
        blockers.append("PAUSE_SCHEMA_MISMATCH")

    print("== PAUSE CODE APPLY PRECHECK ==")
    print("campaigns             : {0}".format(len(campaign_ids)))
    print("campaigns_present_vpn : {0}".format(campaigns_present))
    print("source_pause_rows     : {0}".format(len(source_rows)))
    print("target_existing_rows  : {0}".format(len(existing)))
    print("common_columns        : {0}".format(len(common_cols)))
    print("source_only_columns   : {0}".format(len(source_only)))
    print("target_only_columns   : {0}".format(len(target_only)))
    print("blockers              : {0}".format(blockers))

    if existing:
        print("")
        print("Primeros Pause Codes ya existentes:")
        for campaign_id, pause_code in existing[:30]:
            print("  {0} / {1}".format(campaign_id, pause_code))

    if blockers:
        print("ABORT: no se realizaron cambios")
        return 2

    answer = input(
        "Escribe {0} para continuar: ".format(CONFIRM_TEXT)
    ).strip()
    if answer != CONFIRM_TEXT:
        print("Cancelado.")
        return 1

    created = 0
    try:
        for start in range(0, len(source_rows), CHUNK_SIZE):
            chunk = source_rows[start:start + CHUNK_SIZE]
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
            print("Pause Codes creados: {0}/169".format(created))

        found = verify(config, campaign_ids)
        missing = sorted(expected_pairs - found)
        extra = sorted(found - expected_pairs)

        print("")
        print("== FINAL VERIFY ==")
        print("source_rows : {0}".format(len(expected_pairs)))
        print("target_rows : {0}".format(len(found)))
        print("missing     : {0}".format(len(missing)))
        print("extra       : {0}".format(len(extra)))

        if missing:
            print("MISSING:")
            for campaign_id, pause_code in missing[:50]:
                print("  {0}/{1}".format(campaign_id, pause_code))

        if extra:
            print("EXTRA:")
            for campaign_id, pause_code in extra[:50]:
                print("  {0}/{1}".format(campaign_id, pause_code))

        if not missing and not extra and len(found) == 169:
            print("APPLY OK: 169/169 Pause Codes correctos")
            return 0

        print("APPLY TERMINO, PERO LA VERIFICACION NO ES 169/169")
        return 4

    except Exception as exc:
        print("ERROR TYPE: {0}".format(exc.__class__.__name__))
        print("ERROR: {0}".format(str(exc) or repr(exc)))
        traceback.print_exc()
        print("No vuelvas a ejecutar a ciegas; corre primero el plan.")
        return 5


if __name__ == "__main__":
    sys.exit(main())
