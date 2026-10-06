#!/usr/bin/env python3
import os
import sys

import pymysql

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.db import run_readonly_query
from app.services.user_groups import TARGET_GROUPS

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


def source_campaign_ids(cur, groups):
    marks = ",".join(["%s"] * len(groups))
    cur.execute(
        "SELECT user_group, allowed_campaigns "
        "FROM vicidial_user_groups "
        "WHERE user_group IN ({0})".format(marks),
        tuple(groups),
    )
    ids = []
    seen = set()
    for row in cur.fetchall():
        for cid in parse_allowed_campaigns(row.get("allowed_campaigns")):
            if cid not in seen:
                ids.append(cid)
                seen.add(cid)
    return ids


def source_schema(cur):
    cur.execute("SHOW COLUMNS FROM vicidial_pause_codes")
    return [row["Field"] for row in cur.fetchall()]


def target_schema(config):
    rows = run_readonly_query(
        config,
        "SHOW COLUMNS FROM vicidial_pause_codes",
        "master",
    )
    return [row[0] for row in rows]


def main():
    config = load_config()
    groups = [group for group, _ in TARGET_GROUPS]

    source = source_connection()
    try:
        cur = source.cursor()
        campaign_ids = source_campaign_ids(cur, groups)

        marks = ",".join(["%s"] * len(campaign_ids))
        cur.execute(
            "SELECT * FROM vicidial_pause_codes "
            "WHERE campaign_id IN ({0}) "
            "ORDER BY campaign_id, pause_code".format(marks),
            tuple(campaign_ids),
        )
        source_rows = cur.fetchall()
        source_columns = source_schema(cur)
    finally:
        source.close()

    target_columns = target_schema(config)

    quoted = ", ".join(
        "'" + cid.replace("'", "''") + "'"
        for cid in campaign_ids
    )
    target_rows_raw = run_readonly_query(
        config,
        "SELECT campaign_id, pause_code, pause_code_name "
        "FROM vicidial_pause_codes "
        "WHERE campaign_id IN ({0}) "
        "ORDER BY campaign_id, pause_code".format(quoted),
        "master",
    )
    target_rows = [
        {
            "campaign_id": row[0],
            "pause_code": row[1],
            "pause_code_name": row[2] if len(row) > 2 else "",
        }
        for row in target_rows_raw
    ]

    source_map = {
        (str(row["campaign_id"]), str(row["pause_code"])): row
        for row in source_rows
    }
    target_map = {
        (str(row["campaign_id"]), str(row["pause_code"])): row
        for row in target_rows
    }

    missing = [key for key in source_map if key not in target_map]
    existing = [key for key in source_map if key in target_map]

    common_columns = [
        col for col in source_columns if col in target_columns
    ]
    source_only = [
        col for col in source_columns if col not in target_columns
    ]
    target_only = [
        col for col in target_columns if col not in source_columns
    ]

    by_campaign = {}
    for campaign_id, pause_code in source_map:
        by_campaign.setdefault(campaign_id, []).append(pause_code)

    blockers = []
    if len(campaign_ids) != 63:
        blockers.append("UNEXPECTED_CAMPAIGN_COUNT")
    if "campaign_id" not in common_columns:
        blockers.append("PAUSE_SCHEMA_NO_CAMPAIGN_ID")
    if "pause_code" not in common_columns:
        blockers.append("PAUSE_SCHEMA_NO_PAUSE_CODE")

    print("== PAUSE CODE MIGRATION PLAN ==")
    print("campaigns             : {0}".format(len(campaign_ids)))
    print("pause_rows_source     : {0}".format(len(source_rows)))
    print("pause_rows_missing    : {0}".format(len(missing)))
    print("pause_rows_existing   : {0}".format(len(existing)))
    print("common_columns        : {0}".format(len(common_columns)))
    print("source_only_columns   : {0}".format(len(source_only)))
    print("target_only_columns   : {0}".format(len(target_only)))
    print("blockers              : {0}".format(blockers))
    print("")

    print("== PAUSE CODE COUNTS BY CAMPAIGN ==")
    for cid in campaign_ids:
        codes = sorted(by_campaign.get(cid, []))
        print(
            "{0:20} count={1:3} {2}".format(
                cid, len(codes), ",".join(codes)
            )
        )

    print("")
    print("== MISSING PAUSE CODES IN VPN ==")
    for campaign_id, pause_code in missing:
        row = source_map[(campaign_id, pause_code)]
        print(
            "{0:20} {1:12} billable={2} name={3}".format(
                campaign_id,
                pause_code,
                row.get("billable"),
                row.get("pause_code_name"),
            )
        )

    if source_only:
        print("")
        print("SOURCE-ONLY PAUSE COLUMNS:")
        print("  " + ", ".join(source_only))

    if target_only:
        print("")
        print("TARGET-ONLY PAUSE COLUMNS:")
        print("  " + ", ".join(target_only))

    print("")
    print("write_ready: {0}".format(not blockers))
    return 0 if not blockers else 2


if __name__ == "__main__":
    sys.exit(main())
