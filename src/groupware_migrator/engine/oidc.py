"""OIDC/OAuth2 authorization-code flow helpers for user SSO login."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import jwt
from jwt.algorithms import RSAAlgorithm, ECAlgorithm


@dataclass
class OIDCProviderConfig:
    id: str
    name: str
    client_id: str
    client_secret: str
    issuer: str
    discovery_url: str = ""
    scope: str = "openid email profile"
    admin_claim: str = ""
    admin_claim_value: str = ""

    @property
    def effective_discovery_url(self) -> str:
        return self.discovery_url or f"{self.issuer.rstrip('/')}/.well-known/openid-configuration"


class _Cache:
    """Simple TTL cache for OIDC discovery documents and JWKS."""

    def __init__(self, ttl: int = 300) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry and time.monotonic() - entry[0] < self._ttl:
            return entry[1]
        return None

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)


_cache = _Cache()


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def get_discovery(discovery_url: str) -> dict:
    cached = _cache.get(discovery_url)
    if cached is not None:
        return cached
    doc = _fetch_json(discovery_url)
    _cache.set(discovery_url, doc)
    return doc


def get_jwks(jwks_uri: str) -> dict:
    cached = _cache.get(jwks_uri)
    if cached is not None:
        return cached
    doc = _fetch_json(jwks_uri)
    _cache.set(jwks_uri, doc)
    return doc


def build_authorization_url(
    provider: OIDCProviderConfig,
    *,
    redirect_uri: str,
    state: str,
    nonce: str,
) -> str:
    discovery = get_discovery(provider.effective_discovery_url)
    params = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "scope": provider.scope,
        "state": state,
        "nonce": nonce,
    }
    return f"{discovery['authorization_endpoint']}?{urllib.parse.urlencode(params)}"


def exchange_code(
    provider: OIDCProviderConfig,
    *,
    code: str,
    redirect_uri: str,
) -> dict:
    """POST to the token endpoint and return the raw token response."""
    discovery = get_discovery(provider.effective_discovery_url)
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
    }).encode()
    req = urllib.request.Request(
        discovery["token_endpoint"],
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def validate_id_token(
    provider: OIDCProviderConfig,
    id_token: str,
    *,
    nonce: str,
) -> dict:
    """Validate signature, claims, nonce, issuer. Return verified claims."""
    discovery = get_discovery(provider.effective_discovery_url)
    jwks = get_jwks(discovery["jwks_uri"])

    header = jwt.get_unverified_header(id_token)
    kid = header.get("kid")
    alg = header.get("alg", "RS256")

    _ALG_MAP = {
        "RS256": RSAAlgorithm,
        "RS384": RSAAlgorithm,
        "RS512": RSAAlgorithm,
        "ES256": ECAlgorithm,
        "ES384": ECAlgorithm,
        "ES512": ECAlgorithm,
    }
    algo_cls = _ALG_MAP.get(alg, RSAAlgorithm)

    for key_data in jwks.get("keys", []):
        if kid and key_data.get("kid") != kid:
            continue
        public_key = algo_cls.from_jwk(json.dumps(key_data))
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=[alg],
            audience=provider.client_id,
        )
        if claims.get("nonce") != nonce:
            raise ValueError("nonce mismatch in ID token")
        iss = claims.get("iss", "").rstrip("/")
        if iss != provider.issuer.rstrip("/"):
            raise ValueError(f"issuer mismatch: got {iss!r}")
        return claims

    raise ValueError("no matching JWK found for the ID token's kid")


def make_state(jwt_secret: str) -> tuple[str, str]:
    """Return (nonce, signed_state). State embeds nonce + HMAC for CSRF protection."""
    nonce = secrets.token_urlsafe(16)
    sig = hmac.new(jwt_secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return nonce, f"{nonce}.{sig}"


def verify_state(jwt_secret: str, state: str) -> str:
    """Verify state signature and return nonce, raise ValueError if invalid."""
    try:
        nonce, sig = state.rsplit(".", 1)
    except ValueError:
        raise ValueError("malformed state parameter")
    expected = hmac.new(jwt_secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("state signature invalid (possible CSRF)")
    return nonce


# --- IdP presets -------------------------------------------------------

IDP_PRESETS: list[dict] = [
    {
        "id": "keycloak",
        "name": "Keycloak",
        "issuer_template": "https://{host}/realms/{realm}",
        "discovery_url_template": "https://{host}/realms/{realm}/.well-known/openid-configuration",
        "scope": "openid email profile",
        "notes": "Replace {host} with your Keycloak server hostname and {realm} with the realm name.",
    },
    {
        "id": "okta",
        "name": "Okta",
        "issuer_template": "https://{org_domain}/oauth2/default",
        "discovery_url_template": "https://{org_domain}/oauth2/default/.well-known/openid-configuration",
        "scope": "openid email profile",
        "notes": "Replace {org_domain} with your Okta org domain (e.g. company.okta.com).",
    },
    {
        "id": "auth0",
        "name": "Auth0",
        "issuer_template": "https://{domain}",
        "discovery_url_template": "https://{domain}/.well-known/openid-configuration",
        "scope": "openid email profile",
        "notes": "Replace {domain} with your Auth0 domain (e.g. company.eu.auth0.com).",
    },
    {
        "id": "entra",
        "name": "Microsoft Entra ID (Azure AD)",
        "issuer_template": "https://login.microsoftonline.com/{tenant_id}/v2.0",
        "discovery_url_template": "https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration",
        "scope": "openid email profile",
        "notes": "Replace {tenant_id} with your Azure tenant ID or 'common' for multi-tenant.",
    },
    {
        "id": "google",
        "name": "Google Workspace",
        "issuer_template": "https://accounts.google.com",
        "discovery_url_template": "https://accounts.google.com/.well-known/openid-configuration",
        "scope": "openid email profile",
        "notes": "Configure an OAuth2 client in Google Cloud Console. Restrict to your domain via hd claim if needed.",
    },
]
