from __future__ import annotations

from fastapi import APIRouter

from groupware_migrator.providers import get_provider_presets


def create_providers_router() -> APIRouter:
    router = APIRouter()

    @router.get("/providers")
    def list_providers() -> dict:
        return {"items": get_provider_presets()}

    return router
