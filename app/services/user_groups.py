from app.db import run_readonly_query, run_write_script


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



def _quote_identifier(value):
    return "`{0}`".format(str(value).replace("`", "``"))


def _literal(value):
    if value is None:
        return "NULL"
    return "'{0}'".format(_sql_quote(value))


def apply_user_group_plan(config):
    plan = build_user_group_plan(config)
    create_items = [
        item for item in plan["items"]
        if item["status"] == "CREATE"
    ]

    if not create_items:
        return {
            "created": 0,
            "message": "Todos los User Groups objetivo ya existen",
            "plan": build_user_group_plan(config),
        }

    columns = get_available_columns(config)
    if not columns:
        raise RuntimeError(
            "No se pudieron detectar columnas de vicidial_user_groups"
        )

    statements = ["START TRANSACTION"]

    for item in create_items:
        target = item["user_group"]
        allowed_campaigns = item["allowed_campaigns"]

        select_values = []
        for column in columns:
            if column == "user_group":
                select_values.append(_literal(target))
            elif column == "group_name":
                select_values.append(_literal(target))
            elif column == "allowed_campaigns":
                select_values.append(_literal(allowed_campaigns))
            elif column in (
                "agent_status_viewable_groups",
                "admin_viewable_groups",
                "agent_allowed_chat_groups",
            ):
                select_values.append(
                    _literal(" {0}  ".format(target))
                )
            else:
                select_values.append(_quote_identifier(column))

        statements.append(
            "INSERT INTO vicidial_user_groups ({columns}) "
            "SELECT {values} "
            "FROM vicidial_user_groups "
            "WHERE user_group={template} "
            "AND NOT EXISTS ("
            "SELECT 1 FROM vicidial_user_groups WHERE user_group={target}"
            ")".format(
                columns=", ".join(_quote_identifier(c) for c in columns),
                values=", ".join(select_values),
                template=_literal(LEGACY_GROUP),
                target=_literal(target),
            )
        )

    statements.append("COMMIT")
    run_write_script(config, ";\n".join(statements) + ";", "master")

    after = build_user_group_plan(config)
    still_missing = [
        item["user_group"]
        for item in after["items"]
        if item["status"] == "CREATE"
    ]

    if still_missing:
        raise RuntimeError(
            "La escritura termino pero siguen faltando grupos: {0}".format(
                ", ".join(still_missing)
            )
        )

    return {
        "created": len(create_items),
        "created_groups": [item["user_group"] for item in create_items],
        "legacy_preserved": after["legacy_exists"],
        "legacy_group": LEGACY_GROUP,
        "plan": after,
    }
