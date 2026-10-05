import re
import unicodedata


def normalize_token(value):
    value = (value or "").strip().upper()
    value = unicodedata.normalize("NFD", value)
    value = "".join(
        ch for ch in value
        if unicodedata.category(ch) != "Mn"
    )
    value = re.sub(r"[^A-Z0-9]", "", value)
    return value


def split_name(full_name):
    parts = [part for part in (full_name or "").strip().split() if part]
    return parts


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


def choose_username(first_name, paternal_surname, existing_users):
    existing = set(normalize_token(user) for user in existing_users)
    candidates = build_username_candidates(first_name, paternal_surname)

    for candidate in candidates:
        if candidate not in existing:
            return {
                "username": candidate,
                "collision": False,
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
