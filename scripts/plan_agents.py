#!/usr/bin/env python3
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.services.agent_bulk import build_agent_plan


def main():
    config = load_config()
    plan = build_agent_plan(config)

    print("== AGENT BULK PLAN ==")
    for key in (
        "source_file", "sheet", "target_count", "group_count",
        "create_count", "update_count", "correct_count",
        "clone_fields_count", "write_ready",
    ):
        print("{0:18}: {1}".format(key, plan[key]))

    print("duplicate_users   : {0}".format(len(plan["duplicate_users"])))
    print("duplicate_ext     : {0}".format(len(plan["duplicate_extensions"])))
    print("missing_defaults  : {0}".format(len(plan["missing_defaults"])))
    print("missing_templates : {0}".format(len(plan["missing_templates"])))
    print("missing_clone_cols: {0}".format(len(plan["missing_clone_schema"])))
    print("blockers          : {0}".format(plan["blockers"]))

    print("")
    print("== GROUP TEMPLATE STATUS ==")
    for group in sorted(plan["template_status"]):
        item = plan["template_status"][group]
        print(
            "{0:26} template={1:20} exists={2} active={3} level={4}".format(
                group,
                str(item.get("template_user") or "-"),
                item.get("exists"),
                item.get("active"),
                item.get("user_level"),
            )
        )

    if plan["missing_defaults"]:
        print("MISSING DEFAULTS:")
        for group in plan["missing_defaults"]:
            print("  " + group)

    if plan["missing_templates"]:
        print("MISSING TEMPLATE ROWS:")
        for group in plan["missing_templates"]:
            print("  " + group)

    if plan["missing_clone_schema"]:
        print("MISSING CLONE COLUMNS:")
        for field in plan["missing_clone_schema"]:
            print("  " + field)

    print("")
    print("== FIRST 20 ACTIONS ==")
    for item in plan["items"][:20]:
        print(
            "{status:14} {user:18} {group:26} ext={extension} template={template}".format(
                status=item["status"],
                user=item["user"],
                group=item["user_group"],
                extension=item["extension"],
                template=item.get("template_user") or "-",
            )
        )

    return 0 if plan["write_ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
