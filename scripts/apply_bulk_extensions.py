#!/usr/bin/env python3
import os
import sys
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.services.extension_bulk import build_bulk_plan, apply_bulk


def main():
    config = load_config()
    plan = build_bulk_plan(config)

    print("== BULK EXTENSION PLAN ==")
    print("base_count      : {0}".format(plan["base_count"]))
    print("create_count    : {0}".format(plan["create_count"]))
    print("exists_count    : {0}".format(plan["exists_count"]))
    print("skipped_count   : {0}".format(plan["skipped_count"]))
    print("conflict_count  : {0}".format(plan["conflict_count"]))
    print("write_ready     : {0}".format(plan["write_ready"]))

    if plan["conflict_count"]:
        print("ABORT: hay conflictos")
        return 2

    answer = input("Escribe APPLY para continuar: ").strip()
    if answer != "APPLY":
        print("Cancelado.")
        return 1

    try:
        result = apply_bulk(config)
        print("== RESULTADO ==")
        print(result)
        return 0 if result.get("ok") else 3
    except Exception as exc:
        print("ERROR TYPE: {0}".format(exc.__class__.__name__))
        print("ERROR: {0}".format(str(exc) or repr(exc)))
        traceback.print_exc()
        return 4


if __name__ == "__main__":
    sys.exit(main())
