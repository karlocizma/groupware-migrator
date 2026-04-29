from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from groupware_migrator.models import AuthMode, ConnectionConfig


def build_xoauth2_string(username: str, access_token: str) -> str:
    user_value = username.strip()
    token_value = access_token.strip()
    if not user_value:
        raise ValueError("XOAUTH2 requires a non-empty username.")
    if not token_value:
        raise ValueError("XOAUTH2 requires a non-empty access token.")
    return f"user={user_value}\x01auth=Bearer {token_value}\x01\x01"


def _refresh_access_token(config: ConnectionConfig) -> str:
    if not config.oauth_token_url:
        raise ValueError("Missing OAuth token endpoint URL (oauth_token_url).")
    if not config.oauth_refresh_token:
        raise ValueError("Missing OAuth refresh token (oauth_refresh_token).")
    if not config.oauth_client_id:
        raise ValueError("Missing OAuth client ID (oauth_client_id).")
    if not config.oauth_client_secret:
        raise ValueError("Missing OAuth client secret (oauth_client_secret).")

    request_payload = {
        "grant_type": "refresh_token",
        "refresh_token": config.oauth_refresh_token,
        "client_id": config.oauth_client_id,
        "client_secret": config.oauth_client_secret,
    }
    if config.oauth_scope:
        request_payload["scope"] = config.oauth_scope

    request = Request(
        config.oauth_token_url,
        data=urlencode(request_payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(int(config.timeout_seconds), 1)) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="ignore").strip()
        raise RuntimeError(
            f"OAuth token refresh failed with HTTP {exc.code}: {error_payload or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"OAuth token refresh request failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("OAuth token endpoint returned invalid JSON.") from exc

    access_token = str(response_payload.get("access_token", "")).strip()
    if not access_token:
        raise RuntimeError("OAuth token endpoint response missing access_token.")
    return access_token


def resolve_oauth_access_token(config: ConnectionConfig) -> str:
    if config.auth_mode is not AuthMode.OAUTH2:
        raise ValueError("OAuth token resolution requires auth_mode='oauth2'.")
    if config.oauth_access_token:
        return config.oauth_access_token
    return _refresh_access_token(config)
