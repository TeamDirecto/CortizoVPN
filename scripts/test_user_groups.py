#!/usr/bin/env python3

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.services.user_groups import get_available_columns, get_user_groups


def main():
    config = load_config()

    columns = get_available_columns(config)
    print("Columnas detectadas en vicidial_user_groups:")
    print(", ".join(columns))
    print("")

    groups = get_user_groups(config)

    print("MASTER: {0} user_groups encontrados".format(len(groups)))
    print("")
    print("{0:<25} {1:<40} {2}".format("USER_GROUP", "GROUP_NAME", "ACTIVE"))
    print("-" * 75)

    for group in groups:
        active = group.get("active")
        if active is None:
            active = "-"

        print(
            "{0:<25} {1:<40} {2}".format(
                (group.get("user_group") or "")[:25],
                (group.get("group_name") or "")[:40],
                active,
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
