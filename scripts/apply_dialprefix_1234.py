#!/usr/bin/env python3
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.db import run_readonly_query, run_write_script
from app.services.user_groups import TARGET_GROUPS

TARGET_PREFIX = "1234"
CONFIRM_TEXT = "SET_DIALPREFIX_1234"


def sql_literal(value):
    raw = str(value).encode("utf-8")
    return "CONVERT(X'{0}' USING utf8mb4)".format(raw.hex())


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


def main():
    config = load_config()
    groups = [group for group, _ in TARGET_GROUPS]

    quoted_groups = ", ".join(sql_literal(group) for group in groups)
    group_rows = run_readonly_query(
        config,
        "SELECT user_group, allowed_campaigns "
        "FROM vicidial_user_groups "
        "WHERE user_group IN ({0}) "
        "ORDER BY user_group".format(quoted_groups),
        "master",
    )

    campaign_ids = []
    seen = set()
    for row in group_rows:
        for campaign_id in parse_allowed_campaigns(row[1] if len(row) > 1 else ""):
            if campaign_id not in seen:
                campaign_ids.append(campaign_id)
                seen.add(campaign_id)

    columns = [
        row[0]
        for row in run_readonly_query(
            config,
            "SHOW COLUMNS FROM vicidial_campaigns",
            "master",
        )
    ]

    blockers = []
    if len(group_rows) != 18:
        blockers.append("UNEXPECTED_USER_GROUP_COUNT")
    if len(campaign_ids) != 63:
        blockers.append("UNEXPECTED_CAMPAIGN_COUNT")
    if "dial_prefix" not in columns:
        blockers.append("MISSING_DIAL_PREFIX_COLUMN")
    if "manual_dial_prefix" not in columns:
        blockers.append("MISSING_MANUAL_DIAL_PREFIX_COLUMN")

    if campaign_ids:
        quoted_campaigns = ", ".join(
            sql_literal(campaign_id) for campaign_id in campaign_ids
        )
        rows = run_readonly_query(
            config,
            "SELECT campaign_id, dial_prefix, manual_dial_prefix "
            "FROM vicidial_campaigns "
            "WHERE campaign_id IN ({0}) "
            "ORDER BY campaign_id".format(quoted_campaigns),
            "master",
        )
    else:
        rows = []

    if len(rows) != 63:
        blockers.append("TARGET_CAMPAIGNS_INCOMPLETE")

    already_correct = 0
    to_change = []
    for row in rows:
        dial_prefix = str(row[1] or "")
        manual_prefix = str(row[2] or "")
        if dial_prefix == TARGET_PREFIX and manual_prefix == TARGET_PREFIX:
            already_correct += 1
        else:
            to_change.append((str(row[0]), dial_prefix, manual_prefix))

    print("== DIAL PREFIX PRECHECK ==")
    print("user_groups         : {0}".format(len(group_rows)))
    print("campaigns           : {0}".format(len(campaign_ids)))
    print("campaigns_found     : {0}".format(len(rows)))
    print("already_1234        : {0}".format(already_correct))
    print("campaigns_to_change : {0}".format(len(to_change)))
    print("blockers            : {0}".format(blockers))
    print("")

    if to_change:
        print("== CURRENT VALUES ==")
        for campaign_id, dial_prefix, manual_prefix in to_change:
            print(
                "{0:20} dial_prefix={1!r} manual_dial_prefix={2!r}".format(
                    campaign_id, dial_prefix, manual_prefix
                )
            )
        print("")

    if blockers:
        print("ABORT: no se realizaron cambios")
        return 2

    if not to_change:
        print("Nada que cambiar: las 63 campañas ya tienen ambos prefijos en 1234.")
        return 0

    answer = input(
        "Escribe {0} para aplicar 1234 a ambos campos: ".format(CONFIRM_TEXT)
    ).strip()

    if answer != CONFIRM_TEXT:
        print("Cancelado.")
        return 1

    quoted_campaigns = ", ".join(
        sql_literal(campaign_id) for campaign_id in campaign_ids
    )

    run_write_script(
        config,
        "UPDATE vicidial_campaigns "
        "SET dial_prefix={0}, manual_dial_prefix={0} "
        "WHERE campaign_id IN ({1});".format(
            sql_literal(TARGET_PREFIX),
            quoted_campaigns,
        ),
        "master",
    )

    verify = run_readonly_query(
        config,
        "SELECT "
        "COUNT(*) AS total, "
        "SUM(dial_prefix={0}) AS dial_ok, "
        "SUM(manual_dial_prefix={0}) AS manual_ok, "
        "SUM(dial_prefix={0} AND manual_dial_prefix={0}) AS both_ok "
        "FROM vicidial_campaigns "
        "WHERE campaign_id IN ({1})".format(
            sql_literal(TARGET_PREFIX),
            quoted_campaigns,
        ),
        "master",
    )

    row = verify[0]
    total = int(row[0] or 0)
    dial_ok = int(row[1] or 0)
    manual_ok = int(row[2] or 0)
    both_ok = int(row[3] or 0)

    print("")
    print("== FINAL VERIFY ==")
    print("total     : {0}".format(total))
    print("dial_ok   : {0}".format(dial_ok))
    print("manual_ok : {0}".format(manual_ok))
    print("both_ok   : {0}".format(both_ok))

    if total == 63 and dial_ok == 63 and manual_ok == 63 and both_ok == 63:
        print("APPLY OK: 63/63 campañas con dial_prefix=1234 y manual_dial_prefix=1234")
        return 0

    print("ERROR: la verificación final no quedó 63/63")
    return 3


if __name__ == "__main__":
    sys.exit(main())
