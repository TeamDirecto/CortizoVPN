import os

from openpyxl import load_workbook

from app.db import run_readonly_query


DEFAULT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "Cortizo_rangos_100_desde_161001.xlsx",
)
SHEET_NAME = "Asignación final"


def _read_targets(path=DEFAULT_FILE):
    if not os.path.exists(path):
        raise RuntimeError(
            "No existe el archivo de migracion: {0}".format(path)
        )

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        if SHEET_NAME not in wb.sheetnames:
            raise RuntimeError(
                "No existe la hoja '{0}' en {1}".format(SHEET_NAME, path)
            )

        ws = wb[SHEET_NAME]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        header = [str(value).strip() if value is not None else "" for value in rows[0]]
        indexes = {name: idx for idx, name in enumerate(header)}

        required = ["User Group", "User", "Nombre", "Nueva extensión"]
        missing = [name for name in required if name not in indexes]
        if missing:
            raise RuntimeError(
                "Faltan columnas requeridas en Excel: {0}".format(
                    ", ".join(missing)
                )
            )

        targets = []
        for row in rows[1:]:
            user = row[indexes["User"]]
            user_group = row[indexes["User Group"]]
            full_name = row[indexes["Nombre"]]
            extension = row[indexes["Nueva extensión"]]

            if user in (None, ""):
                continue

            ext = str(int(extension)) if isinstance(extension, float) else str(extension).strip()

            targets.append({
                "user": str(user).strip(),
                "full_name": str(full_name or "").strip(),
                "user_group": str(user_group or "").strip(),
                "extension": ext,
            })

        return targets
    finally:
        wb.close()


def _available_columns(config):
    rows = run_readonly_query(
        config,
        "SHOW COLUMNS FROM vicidial_users",
        "master",
    )
    return [row[0] for row in rows if row]


def _select_existing_users(config, columns):
    wanted = [
        "user",
        "full_name",
        "user_group",
        "phone_login",
        "phone_pass",
        "active",
        "user_level",
    ]
    selected = [column for column in wanted if column in columns]

    rows = run_readonly_query(
        config,
        "SELECT {0} FROM vicidial_users ORDER BY user".format(
            ", ".join(selected)
        ),
        "master",
    )

    result = {}
    for row in rows:
        item = {}
        for index, column in enumerate(selected):
            item[column] = row[index] if index < len(row) else None
        result[item["user"]] = item
    return result


def _template_candidates(config, columns):
    wanted = [
        "user",
        "full_name",
        "user_group",
        "user_level",
        "active",
    ]
    selected = [column for column in wanted if column in columns]
    if "user_level" not in columns:
        return []

    sql = (
        "SELECT {0} FROM vicidial_users "
        "WHERE user_level=1 ORDER BY user LIMIT 20"
    ).format(", ".join(selected))
    rows = run_readonly_query(config, sql, "master")

    items = []
    for row in rows:
        item = {}
        for index, column in enumerate(selected):
            item[column] = row[index] if index < len(row) else None
        items.append(item)
    return items


def build_agent_plan(config, path=DEFAULT_FILE):
    targets = _read_targets(path)
    columns = _available_columns(config)
    existing = _select_existing_users(config, columns)

    duplicate_users = []
    duplicate_extensions = []
    seen_users = {}
    seen_extensions = {}

    for target in targets:
        user = target["user"]
        extension = target["extension"]
        if user in seen_users:
            duplicate_users.append(user)
        seen_users[user] = target

        if extension in seen_extensions:
            duplicate_extensions.append(extension)
        seen_extensions[extension] = target

    items = []
    create_count = 0
    update_count = 0
    correct_count = 0

    for target in targets:
        current = existing.get(target["user"])
        desired = {
            "user": target["user"],
            "full_name": target["full_name"],
            "user_group": target["user_group"],
            "phone_login": target["extension"],
            "phone_pass": target["extension"],
            "active": "Y",
            "user_level": "1",
        }

        if current is None:
            status = "CREATE"
            create_count += 1
        else:
            mismatched = []
            for key in [
                "full_name",
                "user_group",
                "phone_login",
                "phone_pass",
                "active",
                "user_level",
            ]:
                if key not in current:
                    continue
                if str(current.get(key) or "") != str(desired.get(key) or ""):
                    mismatched.append(key)

            if mismatched:
                status = "UPDATE"
                update_count += 1
            else:
                status = "EXISTS_CORRECT"
                correct_count += 1

        items.append({
            "status": status,
            "user": target["user"],
            "full_name": target["full_name"],
            "user_group": target["user_group"],
            "extension": target["extension"],
            "desired": desired,
            "current": current,
        })

    templates = _template_candidates(config, columns)

    return {
        "source_file": path,
        "sheet": SHEET_NAME,
        "target_count": len(targets),
        "create_count": create_count,
        "update_count": update_count,
        "correct_count": correct_count,
        "duplicate_users": sorted(set(duplicate_users)),
        "duplicate_extensions": sorted(set(duplicate_extensions)),
        "template_candidates": templates,
        "write_ready": (
            not duplicate_users
            and not duplicate_extensions
            and (create_count == 0 or len(templates) > 0)
        ),
        "items": items,
    }
