from __future__ import annotations

USER_GROUPS_SQL = """
SELECT
    user_group,
    group_name,
    allowed_campaigns,
    active
FROM vicidial_user_groups
ORDER BY user_group
""".strip()


def get_user_groups_sql() -> str:
    return USER_GROUPS_SQL
