#!/usr/bin/env python3

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config import load_config
from app.ssh import test_direct, test_via_jump


def main():
    config = load_config()
    raw = config.raw

    failures = 0
    dialers = raw.get("dialers", {})

    print("== Dialers por WAN ==")
    for name, node in dialers.items():
        if not node.get("enabled", True):
            print(
                "[SKIP] {0:6} {1}:{2} -> deshabilitado temporalmente".format(
                    name.upper(), node["wan_ip"], config.ssh.port
                )
            )
            continue

        host = node["wan_ip"]
        result = test_direct(host, config)
        state = "OK" if result.ok else "ERROR"
        print(
            "[{0}] {1:6} {2}:{3} -> {4}".format(
                state, name.upper(), host, config.ssh.port, result.detail
            )
        )
        if not result.ok:
            failures += 1

    print("\n== Bases por segundo salto ==")
    databases = raw.get("database", {})
    for name, db in databases.items():
        jump_name = db["jump_host"]
        jump = dialers.get(jump_name)
        if not jump:
            print(
                "[ERROR] {0:6} jump_host desconocido: {1}".format(
                    name.upper(), jump_name
                )
            )
            failures += 1
            continue

        if not jump.get("enabled", True):
            print(
                "[ERROR] {0:6} jump_host {1} esta deshabilitado".format(
                    name.upper(), jump_name.upper()
                )
            )
            failures += 1
            continue

        jump_host = jump["wan_ip"]
        target_host = db["host"]
        target_port = int(db.get("port", config.ssh.port))

        result = test_via_jump(jump_host, target_host, target_port, config)
        state = "OK" if result.ok else "ERROR"
        print(
            "[{0}] {1:6} {2}:{3} via {4} ({5}) -> {6}".format(
                state,
                name.upper(),
                target_host,
                target_port,
                jump_name.upper(),
                jump_host,
                result.detail,
            )
        )
        if not result.ok:
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
