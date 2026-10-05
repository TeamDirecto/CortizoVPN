#!/usr/bin/env python3

from __future__ import annotations

import sys

from app.config import load_config
from app.ssh import test_direct, test_via_jump


def main() -> int:
    config = load_config()
    raw = config.raw

    failures = 0
    dialers = raw.get("dialers", {})

    print("== Dialers por WAN ==")
    for name, node in dialers.items():
        host = node["wan_ip"]
        result = test_direct(host, config)
        state = "OK" if result.ok else "ERROR"
        print(f"[{state}] {name.upper():6} {host}:{config.ssh.port} -> {result.detail}")
        if not result.ok:
            failures += 1

    print("\n== Bases por segundo salto ==")
    databases = raw.get("database", {})
    for name, db in databases.items():
        jump_name = db["jump_host"]
        jump = dialers.get(jump_name)
        if not jump:
            print(f"[ERROR] {name.upper():6} jump_host desconocido: {jump_name}")
            failures += 1
            continue

        jump_host = jump["wan_ip"]
        target_host = db["host"]
        target_port = int(db.get("port", config.ssh.port))

        result = test_via_jump(jump_host, target_host, target_port, config)
        state = "OK" if result.ok else "ERROR"
        print(
            f"[{state}] {name.upper():6} {target_host}:{target_port} "
            f"via {jump_name.upper()} ({jump_host}) -> {result.detail}"
        )
        if not result.ok:
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
