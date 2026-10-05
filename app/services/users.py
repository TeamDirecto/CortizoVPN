import re
import unicodedata

from app.db import run_readonly_query


def normalize_token(value):
    value = (value or "").strip().upper()
    value = unicodedata.normalize("NFD", value)
    value = "".join(
        ch for ch in value
        if unicodedata.category(ch) != "Mn"
    )
    value = re.sub(r"[^A-Z0-9]", "", value)
    return value


def normalize_display_name(value):
    value = (value or "").strip().upper()
    value = unicodedata.normalize("NFD", value)
    value = "".join(
        ch for ch in value
        if unicodedata.category(ch) != "Mn"
    )
    value = re.sub(r"[^A-Z0-9 ]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def build_full_name(first_name, second_name, paternal_surname, maternal_surname):
    parts = [
        normalize_display_name(first_name),
        normalize_display_name(second_name),
        normalize_display_name(paternal_surname),
        normalize_display_name(maternal_surname),
    ]
    return " ".join(part for part in parts if part)


def build_username_candidates(first_name, paternal_surname):
    first = normalize_token(first_name)
    surname = normalize_token(paternal_surname)

    if not first:
        raise ValueError("El primer nombre es obligatorio")
    if not surname:
        raise ValueError("El apellido paterno es obligatorio")

    candidates = []
    for length in range(1, len(surname) + 1):
        candidates.append(surname[:length] + first)

    return candidates


def get_existing_users(config):
    rows = run_readonly_query(
        config,
        "SELECT user FROM vicidial_users ORDER BY user",
        "master",
    )
    return [row[0] for row in rows if row]


def choose_username(first_name, paternal_surname, existing_users):
    existing = set(normalize_token(user) for user in existing_users)
    candidates = build_username_candidates(first_name, paternal_surname)

    for candidate in candidates:
        if candidate not in existing:
            return {
                "username": candidate,
                "collision": candidate != candidates[0],
                "base_candidates": candidates,
            }

    base = candidates[-1]
    suffix = 2
    while "{0}{1}".format(base, suffix) in existing:
        suffix += 1

    return {
        "username": "{0}{1}".format(base, suffix),
        "collision": True,
        "base_candidates": candidates,
    }


def propose_username(config, first_name, second_name, paternal_surname, maternal_surname):
    existing_users = get_existing_users(config)
    result = choose_username(first_name, paternal_surname, existing_users)
    result["full_name"] = build_full_name(
        first_name,
        second_name,
        paternal_surname,
        maternal_surname,
    )
    return result
