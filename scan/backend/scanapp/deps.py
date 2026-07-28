"""FastAPI auth dependencies."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from .auth import ldap
from .auth.oidc import AuthError, verify_token


async def current_user(authorization: str = Header(default="")) -> ldap.LdapUser:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        claims = await verify_token(token)
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    uid = claims["preferred_username"]
    user = ldap.get_user(uid)
    if user is None:
        # authenticated but unknown in LDAP -> minimal identity, no perms
        user = ldap.LdapUser(uid=uid, mail=claims.get("email"))
    return user


async def require_printers(user: ldap.LdapUser = Depends(current_user)) -> ldap.LdapUser:
    if not user.can_scan:
        raise HTTPException(403, "not a member of the scan group")
    return user


def resolve_target(actor: ldap.LdapUser, on_behalf_of: str | None) -> ldap.LdapUser:
    """Return the effective owner. Only admins may scan on behalf of someone else."""
    if not on_behalf_of or on_behalf_of == actor.uid:
        return actor
    if not actor.is_admin:
        raise HTTPException(403, "only admins can scan on behalf of another user")
    target = ldap.get_user(on_behalf_of)
    if target is None:
        raise HTTPException(404, f"unknown user {on_behalf_of}")
    return target
