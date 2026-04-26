import json
from sqlalchemy import text

_ALLOWED_UPDATE_FIELDS = {"name", "include_categories", "exclude_categories", "max_stories", "voice"}


class ProfileRepo:
    def __init__(self, db):
        self.db = db

    def create(self, *, name: str, include_categories=None, exclude_categories=None,
               max_stories: int = 8, voice: str | None = None) -> dict:
        row = self.db.execute(text("""
            INSERT INTO data.profiles (name, include_categories, exclude_categories, max_stories, voice)
            VALUES (:name, :inc, :exc, :max_stories, :voice)
            RETURNING id, name, include_categories, exclude_categories,
                      max_stories, voice, created_at, updated_at
        """), {
            "name": name,
            "inc": json.dumps(include_categories) if include_categories is not None else None,
            "exc": json.dumps(exclude_categories) if exclude_categories is not None else None,
            "max_stories": max_stories,
            "voice": voice,
        }).mappings().fetchone()
        return _parse(dict(row))

    def get_all(self) -> list[dict]:
        rows = self.db.execute(text("""
            SELECT id, name, include_categories, exclude_categories,
                   max_stories, voice, created_at, updated_at
            FROM data.profiles
            ORDER BY id
        """)).mappings().fetchall()
        return [_parse(dict(r)) for r in rows]

    def get_by_id(self, profile_id: int) -> dict | None:
        row = self.db.execute(text("""
            SELECT id, name, include_categories, exclude_categories,
                   max_stories, voice, created_at, updated_at
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
            RETURNING id, name, include_categories, exclude_categories,
                      max_stories, voice, created_at, updated_at
        """), params).mappings().fetchone()
        return _parse(dict(row)) if row else None


def _parse(row: dict) -> dict:
    for field in ("include_categories", "exclude_categories"):
        v = row.get(field)
        if isinstance(v, str):
            row[field] = json.loads(v)
    return row
