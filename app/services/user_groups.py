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


TARGET_GROUPS = [
    ("CC-CORTIZO-BANCO-AZT", ""),
    ("CC-CORTIZO-BANORTE", ""),
    ("CC-CORTIZO-BBVA", ""),
    ("CC-CORTIZO-GMF", ""),
    ("CC-CORTIZO-LABORATOR", ""),
    ("CC-CORTIZO-LIBERTAD", " Libertad -"),
    ("CC-CORTIZO-NISSAN", ""),
    ("CC-CORTIZO-NISSAN-LA", ""),
    ("CC-CORTIZO-RAPIDAUTO", ""),
    ("CC-CORTIZO-SA", ""),
    ("CC-CORTIZO-SCOTI", ""),
    ("CC-CORTIZO-SCOTI-AUT", ""),
    ("CC-CORTIZO-SCOTI-PR", ""),
    ("CC-CORTIZO-SICREA", ""),
    ("CC-CORTIZO-TOTALPLAY", ""),
    ("CC-CORTIZO-TOYOTA", ""),
    ("CC-CORTIZO-UNIFIN", ""),
    ("CC-CORTIZO-VW", ""),
]

LEGACY_GROUP = "CC-LIBERTAD-CORTIZO"


def _sql_quote(value):
    return str(value).replace("'", "''")


def get_user_group_row(config, user_group):
    available = get_available_columns(config)
    sql = "SELECT {0} FROM vicidial_user_groups WHERE user_group='{1}' LIMIT 1".format(
        ", ".join(available),
        _sql_quote(user_group),
    )
    rows = run_readonly_query(config, sql, "master")
    if not rows:
        return None

    row = rows[0]
    return {
        column: row[index] if index < len(row) else None
        for index, column in enumerate(available)
    }


def build_user_group_plan(config):
    current = get_user_groups(config)
    existing = set(item["user_group"] for item in current)
    template = get_user_group_row(config, LEGACY_GROUP)

    if not template:
        raise RuntimeError(
            "No existe el grupo plantilla {0}".format(LEGACY_GROUP)
        )

    plan = []
    for user_group, allowed_campaigns in TARGET_GROUPS:
        status = "EXISTS" if user_group in existing else "CREATE"
        desired = dict(template)
        desired["user_group"] = user_group
        desired["group_name"] = user_group
        desired["allowed_campaigns"] = allowed_campaigns

        for field in [
            "agent_status_viewable_groups",
            "admin_viewable_groups",
            "agent_allowed_chat_groups",
        ]:
            if field in desired:
                desired[field] = " {0}  ".format(user_group)

        plan.append(
            {
                "user_group": user_group,
                "status": status,
                "allowed_campaigns": allowed_campaigns,
                "source_template": LEGACY_GROUP,
                "desired": desired,
            }
        )

    return {
        "template": LEGACY_GROUP,
        "legacy_exists": LEGACY_GROUP in existing,
        "legacy_action": "DELETE_AT_END" if LEGACY_GROUP in existing else "NONE",
        "create_count": sum(1 for item in plan if item["status"] == "CREATE"),
        "exists_count": sum(1 for item in plan if item["status"] == "EXISTS"),
        "total": len(plan),
        "items": plan,
    }
