import json
from sqlalchemy import text

_ALLOWED_UPDATE_FIELDS = {
    "name", "include_categories", "exclude_categories",
    "max_duration_minutes", "voice", "include_top_stories",
}

_SELECT_COLS = """
    id, name, include_categories, exclude_categories,
    max_duration_minutes, voice, include_top_stories, created_at, updated_at
"""


class ProfileRepo:
    def __init__(self, db):
        self.db = db

    def create(self, *, name: str, include_categories=None, exclude_categories=None,
               max_duration_minutes: int = 5, voice: str | None = None,
               include_top_stories: bool = True) -> dict:
        row = self.db.execute(text(f"""
            INSERT INTO data.profiles
                (name, include_categories, exclude_categories,
                 max_duration_minutes, voice, include_top_stories)
            VALUES (:name, :inc, :exc, :max_duration_minutes, :voice, :include_top_stories)
            RETURNING {_SELECT_COLS}
        """), {
            "name": name,
            "inc": json.dumps(include_categories) if include_categories is not None else None,
            "exc": json.dumps(exclude_categories) if exclude_categories is not None else None,
            "max_duration_minutes": max_duration_minutes,
            "voice": voice,
            "include_top_stories": include_top_stories,
        }).mappings().fetchone()
        return _parse(dict(row))

    def get_all(self) -> list[dict]:
        rows = self.db.execute(text(f"""
            SELECT {_SELECT_COLS}
            FROM data.profiles
            ORDER BY id
        """)).mappings().fetchall()
        return [_parse(dict(r)) for r in rows]

    def get_by_id(self, profile_id: int) -> dict | None:
        row = self.db.execute(text(f"""
            SELECT {_SELECT_COLS}
            FROM data.profiles
            WHERE id = :id
        """), {"id": profile_id}).mappings().fetchone()
        return _parse(dict(row)) if row else None

    def update(self, profile_id: int, updates: dict) -> dict | None:
        safe = {k: v for k, v in updates.items() if k in _ALLOWED_UPDATE_FIELDS}
        if not safe:
            return self.get_by_id(profile_id)

        params: dict = {"id": profile_id}
        set_clauses = ["updated_at = NOW()"]
        for k, v in safe.items():
            if k in ("include_categories", "exclude_categories"):
                params[k] = json.dumps(v) if v is not None else None
            else:
                params[k] = v
            set_clauses.append(f"{k} = :{k}")

        row = self.db.execute(text(f"""
            UPDATE data.profiles
            SET {', '.join(set_clauses)}
            WHERE id = :id
            RETURNING {_SELECT_COLS}
        """), params).mappings().fetchone()
        return _parse(dict(row)) if row else None


def _parse(row: dict) -> dict:
    for field in ("include_categories", "exclude_categories"):
        v = row.get(field)
        if isinstance(v, str):
            row[field] = json.loads(v)
    return row
