"""Placeholder catalog: editor metadata for the separate Web GUI.

``GET /api/v1/placeholders/catalog`` returns every completable token the
backend can actually resolve — built-ins from the dispatch registry plus a
dynamically introspected Faker surface (top-level methods, per-provider
methods with signatures, custom providers included). Nothing here executes a
generator; discovery is side-effect free and cached per locale.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from blaze_hammer.services import build_run_config
from blaze_hammer.templating.catalog import build_catalog
from blaze_hammer.web.dependencies import get_state, require_session
from blaze_hammer.web.models import PlaceholderCatalog

router = APIRouter(prefix="")


@router.get("/placeholders/catalog", response_model=PlaceholderCatalog)
async def placeholders_catalog(request: Request) -> PlaceholderCatalog:
    """Full placeholder/Faker catalog for editor autocomplete (authenticated).

    The response describes the *actual* backend environment: the installed
    Faker version, the configured locale, every built-in placeholder spec,
    all resolvable ``faker.<method>`` names (custom providers included), and
    provider-family paths like ``faker.providers.internet.email`` with their
    real signatures.
    """
    require_session(request)
    state = get_state(request)
    locale: str | None = None
    try:
        cfg = build_run_config({}, profile=None, config_path=state.config_path())
        locale = cfg.faker_locale
    except Exception:  # noqa: BLE001 - catalog must survive a broken YAML
        locale = None
    return PlaceholderCatalog.model_validate(build_catalog(locale))
