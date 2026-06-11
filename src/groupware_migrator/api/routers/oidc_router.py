"""OIDC / OAuth2 authorization-code login flow and provider management."""
from __future__ import annotations

import os
import urllib.error
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from groupware_migrator.api.auth import COOKIE_NAME, create_access_token, get_user_role, require_admin
from groupware_migrator.engine.oidc import (
    IDP_PRESETS,
    OIDCProviderConfig,
    build_authorization_url,
    exchange_code,
    make_state,
    validate_id_token,
    verify_state,
)
from groupware_migrator.engine.state import SQLiteStateStore

_NONCE_COOKIE = "_oidc_nonce"
_REDIRECT_COOKIE = "_oidc_redirect"


def _ttl_hours() -> int:
    return int(os.environ.get("JWT_TTL_HOURS", "8"))


def _provider_from_row(row: dict) -> OIDCProviderConfig:
    return OIDCProviderConfig(
        id=row["id"],
        name=row["name"],
        client_id=row["client_id"],
        client_secret=row["client_secret"],
        issuer=row["issuer"],
        discovery_url=row.get("discovery_url", ""),
        scope=row.get("scope", "openid email profile"),
        admin_claim=row.get("admin_claim", ""),
        admin_claim_value=row.get("admin_claim_value", ""),
    )


def _public_row(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"]}


class CreateOIDCProviderPayload(BaseModel):
    name: str
    client_id: str
    client_secret: str
    issuer: str
    discovery_url: str = ""
    scope: str = "openid email profile"
    admin_claim: str = ""
    admin_claim_value: str = ""


def create_oidc_router(state_store: SQLiteStateStore) -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # Public — list providers available for login
    # ------------------------------------------------------------------

    @router.get("/auth/oidc/providers")
    def list_public_providers() -> list[dict]:
        return [_public_row(r) for r in state_store.list_oidc_providers()]

    @router.get("/auth/oidc/idp-presets")
    def list_idp_presets() -> list[dict]:
        return IDP_PRESETS

    # ------------------------------------------------------------------
    # SSO flow: start → IdP → callback
    # ------------------------------------------------------------------

    @router.get("/auth/oidc/{provider_id}/start")
    def oidc_start(
        provider_id: str,
        request: Request,
        response: Response,
        redirect_after: str = "/",
    ) -> RedirectResponse:
        row = state_store.get_oidc_provider(provider_id)
        if not row:
            raise HTTPException(status_code=404, detail="OIDC provider not found.")
        provider = _provider_from_row(row)

        jwt_secret: str = request.app.state.jwt_secret
        nonce, state = make_state(jwt_secret)

        redirect_uri = str(request.url_for("oidc_callback", provider_id=provider_id))
        try:
            auth_url = build_authorization_url(
                provider, redirect_uri=redirect_uri, state=state, nonce=nonce
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"OIDC discovery failed: {exc}") from exc

        resp = RedirectResponse(auth_url, status_code=302)
        resp.set_cookie(_NONCE_COOKIE, nonce, httponly=True, samesite="lax", max_age=600)
        resp.set_cookie(_REDIRECT_COOKIE, redirect_after, httponly=True, samesite="lax", max_age=600)
        return resp

    @router.get("/auth/oidc/{provider_id}/callback", name="oidc_callback")
    def oidc_callback(
        provider_id: str,
        request: Request,
        response: Response,
        code: str = "",
        state: str = "",
        error: str = "",
    ) -> RedirectResponse:
        if error:
            raise HTTPException(status_code=400, detail=f"IdP returned error: {error}")
        if not code or not state:
            raise HTTPException(status_code=400, detail="Missing code or state.")

        jwt_secret: str = request.app.state.jwt_secret

        try:
            nonce = verify_state(jwt_secret, state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        cookie_nonce = request.cookies.get(_NONCE_COOKIE, "")
        if not cookie_nonce or cookie_nonce != nonce:
            raise HTTPException(status_code=400, detail="nonce mismatch (session expired or CSRF)")

        row = state_store.get_oidc_provider(provider_id)
        if not row:
            raise HTTPException(status_code=404, detail="OIDC provider not found.")
        provider = _provider_from_row(row)

        redirect_uri = str(request.url_for("oidc_callback", provider_id=provider_id))
        try:
            token_resp = exchange_code(provider, code=code, redirect_uri=redirect_uri)
        except urllib.error.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Token exchange error: {exc}") from exc

        id_token = token_resp.get("id_token", "")
        if not id_token:
            raise HTTPException(status_code=502, detail="IdP did not return an id_token.")

        try:
            claims = validate_id_token(provider, id_token, nonce=nonce)
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"ID token validation failed: {exc}") from exc

        email = claims.get("email", "")
        if not email:
            raise HTTPException(status_code=400, detail="ID token contains no email claim.")

        # Provision user on first login
        user = state_store.get_user_by_email(email)
        is_admin = False
        if provider.admin_claim and provider.admin_claim_value:
            claim_val = claims.get(provider.admin_claim)
            if isinstance(claim_val, list):
                is_admin = provider.admin_claim_value in claim_val
            else:
                is_admin = str(claim_val) == provider.admin_claim_value

        if user is None:
            from groupware_migrator.engine.state import hash_password
            import secrets as _secrets
            user_id = state_store.create_user(
                email=email,
                password_hash=hash_password(_secrets.token_urlsafe(32)),
                is_admin=is_admin,
                role="admin" if is_admin else "viewer",
            )
            user = state_store.get_user_by_id(user_id)
        else:
            if is_admin and not user.get("is_admin"):
                state_store.update_user(user["id"], is_admin=True, role="admin")
                user = state_store.get_user_by_id(user["id"])

        role = get_user_role(user)
        session_payload = {
            "sub": user["id"],
            "email": user["email"],
            "is_admin": user.get("is_admin", False),
            "role": role,
        }
        token = create_access_token(session_payload, secret=jwt_secret, ttl_hours=_ttl_hours())

        redirect_after = request.cookies.get(_REDIRECT_COOKIE, "/")
        resp = RedirectResponse(redirect_after, status_code=302)
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax")
        resp.delete_cookie(_NONCE_COOKIE)
        resp.delete_cookie(_REDIRECT_COOKIE)
        return resp

    # ------------------------------------------------------------------
    # Admin — full OIDC provider CRUD
    # ------------------------------------------------------------------

    @router.get("/admin/oidc/providers")
    def admin_list_providers(_admin: dict = Depends(require_admin)) -> list[dict]:
        rows = state_store.list_oidc_providers()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "client_id": r["client_id"],
                "issuer": r["issuer"],
                "discovery_url": r.get("discovery_url", ""),
                "scope": r.get("scope", "openid email profile"),
                "admin_claim": r.get("admin_claim", ""),
                "admin_claim_value": r.get("admin_claim_value", ""),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    @router.post("/admin/oidc/providers", status_code=201)
    def admin_create_provider(
        payload: CreateOIDCProviderPayload,
        _admin: dict = Depends(require_admin),
    ) -> dict:
        provider_id = state_store.create_oidc_provider(
            name=payload.name,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            issuer=payload.issuer,
            discovery_url=payload.discovery_url,
            scope=payload.scope,
            admin_claim=payload.admin_claim,
            admin_claim_value=payload.admin_claim_value,
        )
        return {"id": provider_id, "name": payload.name}

    @router.delete("/admin/oidc/providers/{provider_id}", status_code=204)
    def admin_delete_provider(
        provider_id: str,
        _admin: dict = Depends(require_admin),
    ) -> None:
        if not state_store.delete_oidc_provider(provider_id):
            raise HTTPException(status_code=404, detail="Provider not found.")

    return router
