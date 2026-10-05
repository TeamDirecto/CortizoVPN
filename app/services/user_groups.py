USER_GROUPS_SQL = """
SELECT
    user_group,
    group_name,
    allowed_campaigns,
    active
FROM vicidial_user_groups
ORDER BY user_group
""".strip()


def get_user_groups_sql():
    return USER_GROUPS_SQL


def parse_user_groups(rows):
    groups = []
    for row in rows:
        groups.append(
            {
                "user_group": row[0],
                "group_name": row[1],
                "allowed_campaigns": row[2],
                "active": row[3],
            }
        )
    return groups
