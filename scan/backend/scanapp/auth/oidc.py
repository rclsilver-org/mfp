"""OIDC access-token validation against Keycloak (JWKS cached).

Real OIDC in dev and prod alike; only SCANAPP_OIDC_ISSUER differs (a throwaway
Keycloak in docker for local dev, the real Keycloak in prod).
"""
from __future__ import annotations

import time

import httpx
from authlib.jose import JsonWebToken, JsonWebKey

from ..config import settings

_jwt = JsonWebToken(["RS256"])
_jwks_cache: dict = {"exp": 0.0, "keys": None}
_oidc_conf_cache: dict = {"exp": 0.0, "conf": None}


class AuthError(Exception):
    pass


async def _oidc_config() -> dict:
    now = time.time()
    if _oidc_conf_cache["conf"] and _oidc_conf_cache["exp"] > now:
        return _oidc_conf_cache["conf"]
    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url)
        r.raise_for_status()
        conf = r.json()
    _oidc_conf_cache.update(exp=now + 3600, conf=conf)
    return conf


async def _jwks():
    now = time.time()
    if _jwks_cache["keys"] and _jwks_cache["exp"] > now:
        return _jwks_cache["keys"]
    conf = await _oidc_config()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(conf["jwks_uri"])
        r.raise_for_status()
        keys = JsonWebKey.import_key_set(r.json())
    _jwks_cache.update(exp=now + 3600, keys=keys)
    return keys


async def verify_token(token: str) -> dict:
    """Validate a bearer access token, return its claims dict."""
    try:
        keys = await _jwks()
        claims = _jwt.decode(token, keys)
        claims.validate()  # exp / nbf
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"invalid token: {exc}") from exc

    # issuer
    iss = claims.get("iss", "")
    if iss.rstrip("/") != settings.oidc_issuer.rstrip("/"):
        raise AuthError("issuer mismatch")

    # audience / azp (Keycloak access tokens carry aud=account, azp=<client>)
    aud = claims.get("aud")
    auds = set(aud if isinstance(aud, list) else [aud]) if aud else set()
    azp = claims.get("azp")
    ok = settings.oidc_audience in auds or (settings.oidc_accept_azp and azp == settings.oidc_client_id)
    if not ok:
        raise AuthError("audience mismatch")

    if not claims.get("preferred_username"):
        raise AuthError("no preferred_username claim")
    return claims
