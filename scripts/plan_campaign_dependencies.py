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
        "SELECT user_group, allowed_campaigns FROM vicidial_user_groups "
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


def table_exists_source(cur, table):
    cur.execute("SHOW TABLES LIKE %s", (table,))
    return bool(cur.fetchone())


def table_exists_target(config, table):
    rows = run_readonly_query(
        config,
        "SHOW TABLES LIKE '{0}'".format(table.replace("'", "''")),
        "master",
    )
    return bool(rows)


def source_columns(cur, table):
    cur.execute("SHOW COLUMNS FROM {0}".format(table))
    return [row["Field"] for row in cur.fetchall()]


def target_columns(config, table):
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
        cur = source.cursor()
        campaign_ids = source_campaign_ids(cur, groups)

        marks = ",".join(["%s"] * len(campaign_ids))
        cur.execute(
            "SELECT campaign_id, lead_filter_id, dial_prefix, campaign_cid "
            "FROM vicidial_campaigns "
            "WHERE campaign_id IN ({0}) ORDER BY campaign_id".format(marks),
            tuple(campaign_ids),
        )
        campaign_refs = cur.fetchall()

        # Lead filters referenced by the 63 campaigns.
        filter_ids = sorted(set(
            str(row.get("lead_filter_id") or "").strip()
            for row in campaign_refs
            if str(row.get("lead_filter_id") or "").strip()
        ))

        source_filters = {}
        source_filter_cols = []
        if filter_ids and table_exists_source(cur, "vicidial_lead_filters"):
            source_filter_cols = source_columns(cur, "vicidial_lead_filters")
            marks_f = ",".join(["%s"] * len(filter_ids))
            cur.execute(
                "SELECT * FROM vicidial_lead_filters "
                "WHERE lead_filter_id IN ({0}) "
                "ORDER BY lead_filter_id".format(marks_f),
                tuple(filter_ids),
            )
            source_filters = {
                str(row["lead_filter_id"]): row
                for row in cur.fetchall()
            }

        # Lists attached to migrated campaigns.
        cur.execute(
            "SELECT list_id, list_name, campaign_id, active "
            "FROM vicidial_lists "
            "WHERE campaign_id IN ({0}) "
            "ORDER BY campaign_id, list_id".format(marks),
            tuple(campaign_ids),
        )
        source_lists = cur.fetchall()

    finally:
        source.close()

    # Target filters.
    target_filter_ids = set()
    target_filter_cols = []
    if filter_ids and table_exists_target(config, "vicidial_lead_filters"):
        target_filter_cols = target_columns(config, "vicidial_lead_filters")
        quoted = ", ".join(
            "'" + value.replace("'", "''") + "'"
            for value in filter_ids
        )
        rows = run_readonly_query(
            config,
            "SELECT lead_filter_id FROM vicidial_lead_filters "
            "WHERE lead_filter_id IN ({0})".format(quoted),
            "master",
        )
        target_filter_ids = set(str(row[0]) for row in rows)

    missing_filters = [
        fid for fid in filter_ids
        if fid in source_filters and fid not in target_filter_ids
    ]
    source_missing_filters = [
        fid for fid in filter_ids if fid not in source_filters
    ]

    common_filter_cols = [
        col for col in source_filter_cols if col in target_filter_cols
    ]
    source_only_filter_cols = [
        col for col in source_filter_cols if col not in target_filter_cols
    ]

    # Target list inventory for same campaigns.
    quoted_campaigns = ", ".join(
        "'" + value.replace("'", "''") + "'"
        for value in campaign_ids
    )
    target_lists = run_readonly_query(
        config,
        "SELECT list_id, list_name, campaign_id, active "
        "FROM vicidial_lists "
        "WHERE campaign_id IN ({0}) "
        "ORDER BY campaign_id, list_id".format(quoted_campaigns),
        "master",
    )

    source_list_ids = set(str(row["list_id"]) for row in source_lists)
    target_list_ids = set(str(row[0]) for row in target_lists)
    overlapping_lists = sorted(source_list_ids & target_list_ids)

    by_campaign_src = {}
    for row in source_lists:
        by_campaign_src.setdefault(str(row["campaign_id"]), 0)
        by_campaign_src[str(row["campaign_id"])] += 1

    by_campaign_tgt = {}
    for row in target_lists:
        by_campaign_tgt.setdefault(str(row[2]), 0)
        by_campaign_tgt[str(row[2])] += 1

    print("== CAMPAIGN DEPENDENCY PLAN ==")
    print("campaigns               : {0}".format(len(campaign_ids)))
    print("referenced_filters      : {0}".format(len(filter_ids)))
    print("source_filters_found    : {0}".format(len(source_filters)))
    print("target_filters_found    : {0}".format(len(target_filter_ids)))
    print("missing_filters_target  : {0}".format(len(missing_filters)))
    print("source_missing_filters  : {0}".format(len(source_missing_filters)))
    print("filter_common_cols      : {0}".format(len(common_filter_cols)))
    print("filter_source_only_cols : {0}".format(len(source_only_filter_cols)))
    print("source_lists            : {0}".format(len(source_lists)))
    print("target_lists            : {0}".format(len(target_lists)))
    print("list_id_overlaps        : {0}".format(len(overlapping_lists)))
    print("")

    print("== REFERENCED FILTERS ==")
    if not filter_ids:
        print("NONE")
    else:
        for fid in filter_ids:
            state = (
                "TARGET_EXISTS" if fid in target_filter_ids
                else "SOURCE_MISSING" if fid not in source_filters
                else "COPY"
            )
            name = ""
            row = source_filters.get(fid)
            if row:
                name = str(row.get("lead_filter_name") or "")
            print("{0:20} {1:14} {2}".format(fid, state, name))

    print("")
    print("== LIST COUNTS BY CAMPAIGN ==")
    for cid in campaign_ids:
        print(
            "{0:20} source={1:4} target={2:4}".format(
                cid,
                by_campaign_src.get(cid, 0),
                by_campaign_tgt.get(cid, 0),
            )
        )

    if overlapping_lists:
        print("")
        print("WARNING: LIST IDs ALREADY PRESENT IN VPN:")
        for list_id in overlapping_lists[:100]:
            print("  " + list_id)

    print("")
    print("== DIAL REFERENCES ==")
    for row in campaign_refs:
        cid = str(row.get("campaign_id") or "")
        dial_prefix = str(row.get("dial_prefix") or "")
        campaign_cid = str(row.get("campaign_cid") or "")
        if dial_prefix or campaign_cid:
            print(
                "{0:20} dial_prefix={1!r} campaign_cid={2!r}".format(
                    cid, dial_prefix, campaign_cid
                )
            )

    print("")
    print("NOTE: este plan NO copia listas ni leads.")
    print("NOTE: scripts y call-times se omiten por decisión operativa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
