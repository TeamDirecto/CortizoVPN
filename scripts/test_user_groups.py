#!/usr/bin/env python3

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.db import run_readonly_query
from app.services.user_groups import get_user_groups_sql, parse_user_groups


def main():
    config = load_config()
    rows = run_readonly_query(config, get_user_groups_sql(), "master")
    groups = parse_user_groups(rows)

    print("MASTER: {0} user_groups encontrados".format(len(groups)))
    print("")
    print("{0:<25} {1:<40} {2}".format("USER_GROUP", "GROUP_NAME", "ACTIVE"))
    print("-" * 75)

    for group in groups:
        print(
            "{0:<25} {1:<40} {2}".format(
                group["user_group"][:25],
                group["group_name"][:40],
                group["active"],
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
