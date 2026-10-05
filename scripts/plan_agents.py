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
    print("source_file      : {0}".format(plan["source_file"]))
    print("sheet            : {0}".format(plan["sheet"]))
    print("target_count     : {0}".format(plan["target_count"]))
    print("create_count     : {0}".format(plan["create_count"]))
    print("update_count     : {0}".format(plan["update_count"]))
    print("correct_count    : {0}".format(plan["correct_count"]))
    print("duplicate_users  : {0}".format(len(plan["duplicate_users"])))
    print("duplicate_ext    : {0}".format(len(plan["duplicate_extensions"])))
    print("template_count   : {0}".format(len(plan["template_candidates"])))
    print("write_ready      : {0}".format(plan["write_ready"]))

    if plan["duplicate_users"]:
        print("duplicate user IDs:")
        for value in plan["duplicate_users"][:20]:
            print("  {0}".format(value))

    if plan["duplicate_extensions"]:
        print("duplicate extensions:")
        for value in plan["duplicate_extensions"][:20]:
            print("  {0}".format(value))

    print("")
    print("== TEMPLATE CANDIDATES ==")
    for item in plan["template_candidates"]:
        print(
            "{user} | {full_name} | {user_group} | level={user_level} | active={active}".format(
                user=item.get("user", ""),
                full_name=item.get("full_name", ""),
                user_group=item.get("user_group", ""),
                user_level=item.get("user_level", ""),
                active=item.get("active", ""),
            )
        )

    print("")
    print("== FIRST 20 ACTIONS ==")
    for item in plan["items"][:20]:
        print(
            "{status:14} {user:18} {group:26} ext={extension}".format(
                status=item["status"],
                user=item["user"],
                group=item["user_group"],
                extension=item["extension"],
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
