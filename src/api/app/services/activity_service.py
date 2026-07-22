from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import SHARED_DIR


CATALOG_PATH = SHARED_DIR / "activities" / "exercises.json"
NO_SUBLEVEL_VALUES = {"", "none", "null"}


class ActivityCatalogError(ValueError):
    pass


def load_activity_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise ActivityCatalogError("Exercise catalog not found.")

    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ActivityCatalogError("Exercise catalog contains invalid JSON.") from exc

    if not isinstance(catalog.get("levels"), list):
        raise ActivityCatalogError("Exercise catalog has no levels.")

    return catalog


def normalize_sublevel(value: str | int | None) -> int | None:
    if value is None:
        return None

    normalized = str(value).strip().lower()
    if normalized in NO_SUBLEVEL_VALUES:
        return None

    try:
        return int(normalized)
    except ValueError as exc:
        raise ActivityCatalogError(f"Invalid sublevel: {value}") from exc


def select_catalog_activity(
    *,
    level: str,
    sublevel: str | int | None,
    section: str,
    exercise_number: str,
    line_letter: str,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = catalog or load_activity_catalog()
    wanted_level = level.strip().upper()
    wanted_sublevel = normalize_sublevel(sublevel)
    wanted_section = section.strip().lower()
    wanted_exercise = exercise_number.strip().lower()
    wanted_line = line_letter.strip().lower()

    for level_group in data["levels"]:
        if str(level_group.get("level", "")).strip().upper() != wanted_level:
            continue

        for sublevel_group in level_group.get("sublevels", []):
            if normalize_sublevel(sublevel_group.get("sublevel")) != wanted_sublevel:
                continue

            for exercise in sublevel_group.get("exercises", []):
                if str(exercise.get("section", "")).strip().lower() != wanted_section:
                    continue
                if str(exercise.get("exerciseNumber", "")).strip().lower() != wanted_exercise:
                    continue

                for line in exercise.get("lines", []):
                    if str(line.get("line", "")).strip().lower() != wanted_line:
                        continue

                    activity = dict(line)
                    activity["id"] = "-".join(
                        (
                            wanted_level.lower(),
                            str(wanted_sublevel) if wanted_sublevel is not None else "none",
                            wanted_section,
                            wanted_exercise,
                            wanted_line,
                        )
                    )
                    activity["catalog"] = {
                        "level": wanted_level,
                        "sublevel": wanted_sublevel,
                        "section": exercise.get("section"),
                        "exerciseNumber": str(exercise.get("exerciseNumber")),
                        "line": wanted_line,
                        "sourcePage": exercise.get("sourcePage"),
                        "sourceImage": exercise.get("sourceImage"),
                    }
                    return activity

    raise ActivityCatalogError(
        "Exercise line not found: "
        f"{wanted_level}/{wanted_sublevel}/{wanted_section}/"
        f"{exercise_number}/{wanted_line}"
    )
