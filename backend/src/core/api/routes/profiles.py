from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.platform.db.session import SessionLocal
from core.pipeline.data.profile.repos.profile_repo import ProfileRepo
from core.pipeline.data.bulletin.selector import validate_filter_categories
from core.api.routes.data import _do_assemble_and_audio

router = APIRouter(prefix="/data/profiles", tags=["profiles"])


class ProfileCreate(BaseModel):
    name: str
    include_categories: Optional[list[str]] = None
    exclude_categories: Optional[list[str]] = None
    max_stories: int = 8
    voice: Optional[str] = None


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    include_categories: Optional[list[str]] = None
    exclude_categories: Optional[list[str]] = None
    max_stories: Optional[int] = None
    voice: Optional[str] = None


def _validate_cats(inc, exc) -> list[str]:
    errors: list[str] = []
    if inc:
        errors.extend(validate_filter_categories(inc))
    if exc:
        errors.extend(validate_filter_categories(exc))
    return errors


def _fmt(p: dict) -> dict[str, Any]:
    created_at = p["created_at"]
    updated_at = p["updated_at"]
    return {
        "id": p["id"],
        "name": p["name"],
        "include_categories": p["include_categories"],
        "exclude_categories": p["exclude_categories"],
        "max_stories": p["max_stories"],
        "voice": p["voice"],
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
    }


@router.post("")
def create_profile(req: ProfileCreate):
    errors = _validate_cats(req.include_categories, req.exclude_categories)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    with SessionLocal() as db:
        profile = ProfileRepo(db).create(
            name=req.name,
            include_categories=req.include_categories,
            exclude_categories=req.exclude_categories,
            max_stories=req.max_stories,
            voice=req.voice,
        )
        db.commit()

    return _fmt(profile)


@router.get("")
def list_profiles():
    with SessionLocal() as db:
        profiles = ProfileRepo(db).get_all()
    return {"profiles": [_fmt(p) for p in profiles]}


@router.get("/{profile_id}")
def get_profile(profile_id: int):
    with SessionLocal() as db:
        profile = ProfileRepo(db).get_by_id(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
    return _fmt(profile)


@router.put("/{profile_id}")
def update_profile(profile_id: int, req: ProfileUpdate):
    updates = {k: v for k, v in req.model_dump().items() if k in req.model_fields_set}
    errors = _validate_cats(updates.get("include_categories"), updates.get("exclude_categories"))
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    with SessionLocal() as db:
        profile = ProfileRepo(db).update(profile_id, updates)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        db.commit()

    return _fmt(profile)


@router.post("/{profile_id}/bulletin")
def get_profile_bulletin(profile_id: int):
    with SessionLocal() as db:
        profile = ProfileRepo(db).get_by_id(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")

    result = _do_assemble_and_audio(
        include_categories=profile["include_categories"],
        exclude_categories=profile["exclude_categories"],
        max_stories=profile["max_stories"],
        name=profile["name"],
        voice_override=profile["voice"],
    )
    return {"profile_id": profile_id, "profile_name": profile["name"], **result}
