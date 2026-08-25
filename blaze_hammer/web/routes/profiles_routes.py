"""Profile routes: list and show, resolved by the existing config system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from blaze_hammer.config.loader import PROFILES_DIR, load_profile
from blaze_hammer.errors import ProfileError
from blaze_hammer.web.dependencies import WebState, get_state, require_session
from blaze_hammer.web.models import ProfileDetail, ProfileSummary

router = APIRouter(prefix="/api/profiles")


@router.get("", response_model=list[ProfileSummary])
async def list_profiles(request: Request) -> list[ProfileSummary]:
    require_session(request)
    state: WebState = get_state(request)
    directories = [PROFILES_DIR]
    if state.project_file is not None:
        project_profiles = state.project_file.parent / "profiles"
        if project_profiles != PROFILES_DIR:
            directories.insert(0, project_profiles)

    found: dict[str, Any] = {}
    for directory in directories:
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                found.setdefault(path.stem, path)
    return [ProfileSummary(name=name, path=str(path)) for name, path in sorted(found.items())]


@router.get("/{name}", response_model=ProfileDetail)
async def show_profile(name: str, request: Request) -> ProfileDetail:
    require_session(request)
    state: WebState = get_state(request)
    base_dir = state.project_file.parent if state.project_file is not None else None
    try:
        data = load_profile(name, base_dir)
        path = str(load_profile_path(name, base_dir))
    except ProfileError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return ProfileDetail(
        name=name,
        path=path,
        data=json.loads(json.dumps(data)),
    )


def load_profile_path(name: str, base_dir: Path | None) -> Path:
    from blaze_hammer.config.loader import resolve_profile_path

    return resolve_profile_path(name, base_dir)
