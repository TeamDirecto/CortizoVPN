#!/usr/bin/env python3
import os
import re
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
    text = str(raw or "").strip()
    if not text:
        return []
    result = []
    seen = set()
    for token in re.split(r"\s+", text):
        token = token.strip()
        if not token or token == "-":
            continue
        if token not in seen:
            result.append(token)
            seen.add(token)
    return result

def fetch_source_groups(cursor, groups):
    placeholders = ",".join(["%s"] * len(groups))
    cursor.execute(
        "SELECT user_group, group_name, allowed_campaigns "
        "FROM vicidial_user_groups "
        "WHERE user_group IN ({0}) ORDER BY user_group".format(placeholders),
        tuple(groups),
    )
    return {row["user_group"]: row for row in cursor.fetchall()}

def fetch_source_campaigns(cursor, campaign_ids):
    if not campaign_ids:
        return {}
    placeholders = ",".join(["%s"] * len(campaign_ids))
    cursor.execute(
        "SELECT * FROM vicidial_campaigns "
        "WHERE campaign_id IN ({0}) ORDER BY campaign_id".format(placeholders),
        tuple(campaign_ids),
    )
    return {row["campaign_id"]: row for row in cursor.fetchall()}

def target_campaigns(config, campaign_ids):
    if not campaign_ids:
        return {}
    quoted = ", ".join(
        "'" + value.replace("'", "''") + "'" for value in campaign_ids
    )
    rows = run_readonly_query(
        config,
        "SELECT campaign_id, campaign_name, active "
        "FROM vicidial_campaigns WHERE campaign_id IN ({0}) "
        "ORDER BY campaign_id".format(quoted),
        "master",
    )
    return {
        str(row[0]).upper(): {
            "campaign_id": row[0],
            "campaign_name": row[1] if len(row) > 1 else "",
            "active": row[2] if len(row) > 2 else "",
        }
        for row in rows
    }

def target_groups(config, groups):
    quoted = ", ".join(
        "'" + value.replace("'", "''") + "'" for value in groups
    )
    rows = run_readonly_query(
        config,
        "SELECT user_group, allowed_campaigns "
        "FROM vicidial_user_groups WHERE user_group IN ({0}) "
        "ORDER BY user_group".format(quoted),
        "master",
    )
    return {
        row[0]: row[1] if len(row) > 1 else ""
        for row in rows
    }

def schema_columns_source(cursor, table):
    cursor.execute("SHOW COLUMNS FROM {0}".format(table))
    return [row["Field"] for row in cursor.fetchall()]

def schema_columns_target(config, table):
    rows = run_readonly_query(
        config,
        "SHOW COLUMNS FROM {0}".format(table),
        "master",
    )
    return [row[0] for row in rows]

def main():
    config = load_config()
    groups = [group for group, _ in TARGET_GROUPS]

    source = source_connection()
    try:
        cursor = source.cursor()
        source_groups = fetch_source_groups(cursor, groups)
        missing_source_groups = [
            group for group in groups if group not in source_groups
        ]

        group_campaigns = {}
        all_campaign_ids = []
        seen_campaigns = set()

        for group in groups:
            row = source_groups.get(group)
            campaign_ids = parse_allowed_campaigns(
                row.get("allowed_campaigns") if row else ""
            )
            group_campaigns[group] = campaign_ids
            for campaign_id in campaign_ids:
                if campaign_id not in seen_campaigns:
                    all_campaign_ids.append(campaign_id)
                    seen_campaigns.add(campaign_id)

        source_campaigns = fetch_source_campaigns(cursor, all_campaign_ids)
        source_campaign_columns = schema_columns_source(
            cursor, "vicidial_campaigns"
        )
    finally:
        source.close()

    target_campaign_columns = schema_columns_target(
        config, "vicidial_campaigns"
    )
    common_campaign_columns = [
        col for col in source_campaign_columns
        if col in target_campaign_columns
    ]
    source_only_columns = [
        col for col in source_campaign_columns
        if col not in target_campaign_columns
    ]
    target_only_columns = [
        col for col in target_campaign_columns
        if col not in source_campaign_columns
    ]

    target_group_rows = target_groups(config, groups)
    target_campaign_rows = target_campaigns(config, all_campaign_ids)

    missing_target_groups = [
        group for group in groups if group not in target_group_rows
    ]
    missing_source_campaigns = [
        campaign_id for campaign_id in all_campaign_ids
        if campaign_id not in source_campaigns
    ]

    create_campaigns = [
        campaign_id for campaign_id in all_campaign_ids
        if campaign_id in source_campaigns
        and campaign_id.upper() not in target_campaign_rows
    ]
    existing_campaigns = [
        campaign_id for campaign_id in all_campaign_ids
        if campaign_id.upper() in target_campaign_rows
    ]

    groups_to_update = []
    groups_already_correct = []
    for group in groups:
        source_row = source_groups.get(group)
        if not source_row or group not in target_group_rows:
            continue
        source_allowed = str(source_row.get("allowed_campaigns") or "")
        target_allowed = str(target_group_rows.get(group) or "")
        if source_allowed == target_allowed:
            groups_already_correct.append(group)
        else:
            groups_to_update.append(group)

    case_differences = []
    for campaign_id in all_campaign_ids:
        target_row = target_campaign_rows.get(campaign_id.upper())
        if target_row and str(target_row["campaign_id"]) != str(campaign_id):
            case_differences.append(
                (campaign_id, str(target_row["campaign_id"]))
            )

    blockers = []
    if missing_source_groups:
        blockers.append("SOURCE_USER_GROUP_MISSING")
    if missing_target_groups:
        blockers.append("TARGET_USER_GROUP_MISSING")
    if missing_source_campaigns:
        blockers.append("SOURCE_CAMPAIGN_MISSING")
    if "campaign_id" not in common_campaign_columns:
        blockers.append("CAMPAIGN_SCHEMA_NO_ID")

    print("== CAMPAIGN MIGRATION PLAN ==")
    print("source_groups          : {0}".format(len(source_groups)))
    print("target_groups          : {0}".format(len(target_group_rows)))
    print("unique_campaigns       : {0}".format(len(all_campaign_ids)))
    print("campaigns_create       : {0}".format(len(create_campaigns)))
    print("campaigns_existing     : {0}".format(len(existing_campaigns)))
    print("campaign_id_case_diff  : {0}".format(len(case_differences)))
    print("groups_update          : {0}".format(len(groups_to_update)))
    print("groups_already_correct : {0}".format(len(groups_already_correct)))
    print("common_campaign_cols   : {0}".format(len(common_campaign_columns)))
    print("source_only_cols       : {0}".format(len(source_only_columns)))
    print("target_only_cols       : {0}".format(len(target_only_columns)))
    print("blockers               : {0}".format(blockers))
    print("")

    print("== USER GROUP -> CAMPAIGNS (EHECTO) ==")
    for group in groups:
        source_row = source_groups.get(group)
        raw = source_row.get("allowed_campaigns") if source_row else None
        campaign_ids = group_campaigns.get(group, [])
        print(
            "{0:26} campaigns={1:2} raw={2!r}".format(
                group, len(campaign_ids), raw
            )
        )
        if campaign_ids:
            print("  " + ", ".join(campaign_ids))

    print("")
    print("== CAMPAIGNS ==")
    for campaign_id in all_campaign_ids:
        source_row = source_campaigns.get(campaign_id)
        target_row = target_campaign_rows.get(campaign_id.upper())
        if source_row is None:
            status = "SOURCE_MISSING"
            name = ""
            active = ""
        elif target_row is None:
            status = "CREATE"
            name = source_row.get("campaign_name", "")
            active = source_row.get("active", "")
        else:
            status = "EXISTS"
            name = source_row.get("campaign_name", "")
            active = source_row.get("active", "")
        print(
            "{0:20} {1:8} active={2} name={3}".format(
                campaign_id, status, active, name
            )
        )

    if case_differences:
        print("")
        print("CAMPAIGN ID CASE DIFFERENCES:")
        for source_id, target_id in case_differences:
            print("  source={0} target={1}".format(source_id, target_id))

    if source_only_columns:
        print("")
        print("SOURCE-ONLY CAMPAIGN COLUMNS:")
        print("  " + ", ".join(source_only_columns))

    if target_only_columns:
        print("")
        print("TARGET-ONLY CAMPAIGN COLUMNS:")
        print("  " + ", ".join(target_only_columns))

    print("")
    print("write_ready: {0}".format(not blockers))
    return 0 if not blockers else 2

if __name__ == "__main__":
    sys.exit(main())
