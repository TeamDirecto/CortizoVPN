#!/usr/bin/env python3
import os
import sys
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.db import run_write_script
from app.services.extension_bulk import (
    build_bulk_plan,
    _literal,
    _request_rebuild,
    _wait_rebuild,
    _sip_reload,
    _verify_node_peers,
    target_rows,
    phone_login_for_node,
    NODE_ORDER,
)


def _desired_update_sql(item, where_extension):
    d = item["desired"]
    return (
        "UPDATE phones SET "
        "extension={extension}, dialplan_number={dialplan_number}, "
        "voicemail_id={voicemail_id}, login={login}, pass={password}, "
        "fullname={fullname}, outbound_cid={outbound_cid}, "
        "user_group={user_group} "
        "WHERE extension={where_extension} AND server_ip={server_ip}"
    ).format(
        extension=_literal(d["extension"]),
        dialplan_number=_literal(d["dialplan_number"]),
        voicemail_id=_literal(d["voicemail_id"]),
        login=_literal(d["login"]),
        password=_literal(d["pass"]),
        fullname=_literal(d["fullname"]),
        outbound_cid=_literal(d["outbound_cid"]),
        user_group=_literal(d["user_group"]),
        where_extension=_literal(where_extension),
        server_ip=_literal(d["server_ip"]),
    )


def _repair_chunk(config, items):
    statements = ["START TRANSACTION"]
    for item in items:
        d = item["desired"]
        legacy_extension = item["login"]

        if item["status"] == "MERGE_LEGACY_VARIANT":
            statements.append(
                _desired_update_sql(item, d["extension"])
            )
            statements.append(
                "DELETE FROM phones WHERE extension={extension} "
                "AND server_ip={server_ip}".format(
                    extension=_literal(legacy_extension),
                    server_ip=_literal(d["server_ip"]),
                )
            )
        elif item["status"] == "REKEY_LEGACY_VARIANT":
            statements.append(
                _desired_update_sql(item, legacy_extension)
            )

    statements.append("COMMIT")
    run_write_script(config, ";\n".join(statements) + ";", "master")


def main():
    config = load_config()
    plan = build_bulk_plan(config)

    structural = [
        item for item in plan["items"]
        if item["status"] in ("REKEY_LEGACY_VARIANT", "MERGE_LEGACY_VARIANT")
    ]
    metadata = [
        item for item in plan["items"]
        if item["status"] == "UPDATE_METADATA"
    ]

    print("== PHONE STRUCTURE REPAIR PLAN ==")
    print("structural_repair : {0}".format(len(structural)))
    print("metadata_update   : {0}".format(len(metadata)))
    print("create_count      : {0}".format(plan["create_count"]))
    print("conflict_count    : {0}".format(plan["conflict_count"]))

    if plan["conflict_count"]:
        print("ABORT: hay conflictos")
        return 2

    if not structural:
        print("No hay estructura legacy por reparar.")
        return 0

    print("")
    print("Estructura destino:")
    print("  extension      = base")
    print("  dialplan       = base / 1+base / 2+base / 3+base")
    print("  voicemail_id   = base")
    print("  login          = base / base+b / base+c / base+d")
    print("  pass           = base")
    print("  fullname       = ext base")
    print("  outbound_cid   = 0000000000")
    print("  user_group     = cartera")

    answer = input("Escribe REPAIR para aplicar: ").strip()
    if answer != "REPAIR":
        print("Cancelado.")
        return 1

    try:
        chunk_size = 20
        repaired = 0
        for start in range(0, len(structural), chunk_size):
            chunk = structural[start:start + chunk_size]
            _repair_chunk(config, chunk)
            repaired += len(chunk)
            print("Reparados {0}/{1}".format(repaired, len(structural)))

        ips = sorted(set(
            item["server_ip"] for item in structural if item.get("server_ip")
        ))
        _request_rebuild(config, ips)
        rebuild_ok = _wait_rebuild(config, ips, timeout=120, interval=2)
        print("rebuild_ok: {0}".format(rebuild_ok))

        targets = target_rows()
        for node_name in NODE_ORDER:
            node = config.raw.get("dialers", {}).get(node_name)
            if not node or not node.get("enabled", True):
                print("{0}: SKIP_DISABLED".format(node_name))
                continue

            reload_result = _sip_reload(config, node_name)
            expected = [
                phone_login_for_node(config, row["base_extension"], node_name)
                for row in targets
            ]
            verify_result = _verify_node_peers(config, node_name, expected)
            print("{0}: reload={1} verify={2} found={3}/{4}".format(
                node_name,
                reload_result.get("status"),
                verify_result.get("status"),
                verify_result.get("found"),
                verify_result.get("expected"),
            ))

        after = build_bulk_plan(config)
        remaining = [
            item for item in after["items"]
            if item["status"] in ("REKEY_LEGACY_VARIANT", "MERGE_LEGACY_VARIANT")
        ]
        print("remaining_structural: {0}".format(len(remaining)))
        print("update_count_after: {0}".format(after["update_count"]))
        return 0 if not remaining else 3

    except Exception as exc:
        print("ERROR TYPE: {0}".format(exc.__class__.__name__))
        print("ERROR: {0}".format(str(exc) or repr(exc)))
        traceback.print_exc()
        return 4


if __name__ == "__main__":
    sys.exit(main())
