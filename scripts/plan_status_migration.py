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


def get_campaign_ids_from_source(cursor, groups):
    placeholders = ",".join(["%s"] * len(groups))
    cursor.execute(
        "SELECT user_group, allowed_campaigns "
        "FROM vicidial_user_groups "
        "WHERE user_group IN ({0})".format(placeholders),
        tuple(groups),
    )
    campaign_ids = []
    seen = set()
    for row in cursor.fetchall():
        for campaign_id in parse_allowed_campaigns(row.get("allowed_campaigns")):
            if campaign_id not in seen:
                campaign_ids.append(campaign_id)
                seen.add(campaign_id)
    return campaign_ids


def source_schema(cursor, table):
    cursor.execute("SHOW COLUMNS FROM {0}".format(table))
    return [row["Field"] for row in cursor.fetchall()]


def target_schema(config, table):
    rows = run_readonly_query(
        config,
        "SHOW COLUMNS FROM {0}".format(table),
        "master",
    )
    return [row[0] for row in rows]


def source_campaign_statuses(cursor, campaign_ids):
    if not campaign_ids:
        return []
    placeholders = ",".join(["%s"] * len(campaign_ids))
    cursor.execute(
        "SELECT * FROM vicidial_campaign_statuses "
        "WHERE campaign_id IN ({0}) "
        "ORDER BY campaign_id, status".format(placeholders),
        tuple(campaign_ids),
    )
    return cursor.fetchall()


def target_campaign_statuses(config, campaign_ids):
    if not campaign_ids:
        return []
    quoted = ", ".join(
        "'" + campaign_id.replace("'", "''") + "'"
        for campaign_id in campaign_ids
    )
    rows = run_readonly_query(
        config,
        "SELECT campaign_id, status, status_name, selectable, human_answered, category "
        "FROM vicidial_campaign_statuses "
        "WHERE campaign_id IN ({0}) "
        "ORDER BY campaign_id, status".format(quoted),
        "master",
    )
    result = []
    for row in rows:
        result.append({
            "campaign_id": row[0],
            "status": row[1],
            "status_name": row[2] if len(row) > 2 else "",
            "selectable": row[3] if len(row) > 3 else "",
            "human_answered": row[4] if len(row) > 4 else "",
            "category": row[5] if len(row) > 5 else "",
        })
    return result


def source_global_statuses(cursor, statuses):
    if not statuses:
        return []
    placeholders = ",".join(["%s"] * len(statuses))
    cursor.execute(
        "SELECT * FROM vicidial_statuses "
        "WHERE status IN ({0}) "
        "ORDER BY status".format(placeholders),
        tuple(statuses),
    )
    return cursor.fetchall()


def target_global_statuses(config, statuses):
    if not statuses:
        return []
    quoted = ", ".join(
        "'" + status.replace("'", "''") + "'"
        for status in statuses
    )
    rows = run_readonly_query(
        config,
        "SELECT status, status_name, selectable, human_answered, category "
        "FROM vicidial_statuses "
        "WHERE status IN ({0}) ORDER BY status".format(quoted),
        "master",
    )
    result = []
    for row in rows:
        result.append({
            "status": row[0],
            "status_name": row[1] if len(row) > 1 else "",
            "selectable": row[2] if len(row) > 2 else "",
            "human_answered": row[3] if len(row) > 3 else "",
            "category": row[4] if len(row) > 4 else "",
        })
    return result


def main():
    config = load_config()
    groups = [group for group, _ in TARGET_GROUPS]

    src = source_connection()
    try:
        cur = src.cursor()
        campaign_ids = get_campaign_ids_from_source(cur, groups)
        source_rows = source_campaign_statuses(cur, campaign_ids)

        campaign_status_schema_source = source_schema(
            cur, "vicidial_campaign_statuses"
        )
        campaign_status_schema_target = target_schema(
            config, "vicidial_campaign_statuses"
        )

        status_codes = sorted(set(str(row["status"]) for row in source_rows))
        global_source_rows = source_global_statuses(cur, status_codes)

        global_schema_source = source_schema(cur, "vicidial_statuses")
        global_schema_target = target_schema(config, "vicidial_statuses")
    finally:
        src.close()

    target_rows = target_campaign_statuses(config, campaign_ids)
    target_global_rows = target_global_statuses(config, status_codes)

    source_map = {
        (str(row["campaign_id"]), str(row["status"])): row
        for row in source_rows
    }
    target_map = {
        (str(row["campaign_id"]), str(row["status"])): row
        for row in target_rows
    }

    missing_campaign_statuses = [
        key for key in source_map
        if key not in target_map
    ]
    existing_campaign_statuses = [
        key for key in source_map
        if key in target_map
    ]

    source_global_map = {
        str(row["status"]): row for row in global_source_rows
    }
    target_global_map = {
        str(row["status"]): row for row in target_global_rows
    }

    missing_global_statuses = [
        status for status in source_global_map
        if status not in target_global_map
    ]

    common_campaign_cols = [
        c for c in campaign_status_schema_source
        if c in campaign_status_schema_target
    ]
    source_only_campaign_cols = [
        c for c in campaign_status_schema_source
        if c not in campaign_status_schema_target
    ]

    common_global_cols = [
        c for c in global_schema_source
        if c in global_schema_target
    ]
    source_only_global_cols = [
        c for c in global_schema_source
        if c not in global_schema_target
    ]

    by_campaign = {}
    for campaign_id, status in source_map:
        by_campaign.setdefault(campaign_id, []).append(status)

    blockers = []
    if len(campaign_ids) != 63:
        blockers.append("UNEXPECTED_CAMPAIGN_COUNT")
    if "campaign_id" not in common_campaign_cols:
        blockers.append("CAMPAIGN_STATUS_SCHEMA_NO_CAMPAIGN_ID")
    if "status" not in common_campaign_cols:
        blockers.append("CAMPAIGN_STATUS_SCHEMA_NO_STATUS")
    if "status" not in common_global_cols:
        blockers.append("GLOBAL_STATUS_SCHEMA_NO_STATUS")

    print("== STATUS MIGRATION PLAN ==")
    print("campaigns                : {0}".format(len(campaign_ids)))
    print("campaign_status_rows_src : {0}".format(len(source_rows)))
    print("campaign_status_missing  : {0}".format(len(missing_campaign_statuses)))
    print("campaign_status_existing : {0}".format(len(existing_campaign_statuses)))
    print("unique_status_codes      : {0}".format(len(status_codes)))
    print("global_status_rows_src   : {0}".format(len(global_source_rows)))
    print("global_status_missing    : {0}".format(len(missing_global_statuses)))
    print("campaign_common_cols     : {0}".format(len(common_campaign_cols)))
    print("campaign_source_only     : {0}".format(len(source_only_campaign_cols)))
    print("global_common_cols       : {0}".format(len(common_global_cols)))
    print("global_source_only       : {0}".format(len(source_only_global_cols)))
    print("blockers                 : {0}".format(blockers))
    print("")

    print("== CAMPAIGN STATUS COUNTS ==")
    for campaign_id in campaign_ids:
        statuses = sorted(by_campaign.get(campaign_id, []))
        print(
            "{0:20} count={1:3} {2}".format(
                campaign_id,
                len(statuses),
                ",".join(statuses),
            )
        )

    print("")
    print("== MISSING CAMPAIGN STATUSES IN VPN ==")
    for campaign_id, status in missing_campaign_statuses:
        row = source_map[(campaign_id, status)]
        print(
            "{0:20} {1:10} selectable={2} human={3} category={4} name={5}".format(
                campaign_id,
                status,
                row.get("selectable"),
                row.get("human_answered"),
                row.get("category"),
                row.get("status_name"),
            )
        )

    print("")
    print("== GLOBAL STATUS CODES REQUIRED ==")
    for status in sorted(source_global_map):
        state = "MISSING" if status in missing_global_statuses else "EXISTS"
        row = source_global_map[status]
        print(
            "{0:10} {1:7} selectable={2} human={3} category={4} name={5}".format(
                status,
                state,
                row.get("selectable"),
                row.get("human_answered"),
                row.get("category"),
                row.get("status_name"),
            )
        )

    if source_only_campaign_cols:
        print("")
        print("CAMPAIGN STATUS SOURCE-ONLY COLUMNS:")
        print("  " + ", ".join(source_only_campaign_cols))

    if source_only_global_cols:
        print("")
        print("GLOBAL STATUS SOURCE-ONLY COLUMNS:")
        print("  " + ", ".join(source_only_global_cols))

    print("")
    print("write_ready: {0}".format(not blockers))
    return 0 if not blockers else 2


if __name__ == "__main__":
    sys.exit(main())
