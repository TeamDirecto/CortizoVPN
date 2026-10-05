from app.db import run_readonly_query


BASE_COLUMNS = [
    "user_group",
    "group_name",
    "allowed_campaigns",
]

OPTIONAL_COLUMNS = [
    "active",
]


def get_available_columns(config):
    rows = run_readonly_query(
        config,
        "SHOW COLUMNS FROM vicidial_user_groups",
        "master",
    )
    return [row[0] for row in rows if row]


def get_user_groups(config):
    available = get_available_columns(config)

    columns = []
    for column in BASE_COLUMNS + OPTIONAL_COLUMNS:
        if column in available:
            columns.append(column)

    missing = [column for column in BASE_COLUMNS if column not in available]
    if missing:
        raise RuntimeError(
            "Faltan columnas requeridas en vicidial_user_groups: {0}".format(
                ", ".join(missing)
            )
        )

    sql = "SELECT {0} FROM vicidial_user_groups ORDER BY user_group".format(
        ", ".join(columns)
    )

    rows = run_readonly_query(config, sql, "master")

    groups = []
    for row in rows:
        item = {}
        for index, column in enumerate(columns):
            item[column] = row[index] if index < len(row) else None

        if "active" not in item:
            item["active"] = None

        groups.append(item)

    return groups
