"""LDAP lookups: group membership + user attributes (anonymous read, cached).

Authorization stays in LDAP (same source of truth as the print proxy), while
authentication is done via OIDC. Identity is the OIDC preferred_username, which
equals the LDAP uid.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import ldap3
from ldap3.utils.conv import escape_filter_chars

from ..config import settings


@dataclass
class LdapUser:
    uid: str
    uid_number: int | None = None
    mail: str | None = None
    home_directory: str | None = None
    display_name: str | None = None
    groups: set[str] = field(default_factory=set)

    @property
    def can_scan(self) -> bool:
        return settings.ldap_scan_group in self.groups

    @property
    def is_admin(self) -> bool:
        return settings.ldap_admin_group in self.groups


_cache: dict[str, tuple[float, LdapUser]] = {}
_lock = threading.Lock()


def _server() -> ldap3.Server:
    return ldap3.Server(settings.ldap_url, get_info=ldap3.NONE, connect_timeout=5)


def _lookup(uid: str) -> LdapUser | None:
    conn = ldap3.Connection(_server(), auto_bind=True, receive_timeout=5)
    try:
        # user entry + attributes
        conn.search(
            settings.ldap_user_base,
            f"(uid={escape_filter_chars(uid)})",
            attributes=["uid", "uidNumber", "mail", "homeDirectory", "cn", "displayName"],
        )
        if not conn.entries:
            return None
        e = conn.entries[0]

        def val(attr):
            v = e[attr].value if attr in e else None
            return v

        user = LdapUser(
            uid=uid,
            uid_number=int(val("uidNumber")) if val("uidNumber") is not None else None,
            mail=val("mail"),
            home_directory=val("homeDirectory"),
            display_name=val("displayName") or val("cn"),
        )

        # posixGroups the user belongs to (memberUid)
        conn.search(
            settings.ldap_group_base,
            f"(&(objectClass=posixGroup)(memberUid={escape_filter_chars(uid)}))",
            attributes=["cn"],
        )
        user.groups = {en["cn"].value for en in conn.entries}
        return user
    finally:
        conn.unbind()


def get_user(uid: str) -> LdapUser | None:
    now = time.time()
    with _lock:
        hit = _cache.get(uid)
        if hit and hit[0] > now:
            return hit[1]
    user = _lookup(uid)
    if user is not None:
        with _lock:
            _cache[uid] = (now + settings.ldap_cache_ttl, user)
    return user
