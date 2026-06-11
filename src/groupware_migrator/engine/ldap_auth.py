from __future__ import annotations

import os

import ldap3
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

__all__ = ["LDAPAuthBackend", "LDAPAuthError"]


class LDAPAuthError(Exception):
    """Raised when LDAP connectivity or configuration prevents authentication."""


def _ldap_config() -> dict:
    use_ssl = os.environ.get("LDAP_USE_SSL", "false").lower() == "true"
    default_port = 636 if use_ssl else 389
    return {
        "host": os.environ.get("LDAP_HOST", ""),
        "port": int(os.environ.get("LDAP_PORT", str(default_port))),
        "use_ssl": use_ssl,
        "use_starttls": os.environ.get("LDAP_USE_STARTTLS", "false").lower() == "true",
        "bind_dn": os.environ.get("LDAP_BIND_DN", ""),
        "bind_password": os.environ.get("LDAP_BIND_PASSWORD", ""),
        "base_dn": os.environ.get("LDAP_BASE_DN", ""),
        "user_filter": os.environ.get("LDAP_USER_FILTER", "(userPrincipalName={email})"),
        "email_attr": os.environ.get("LDAP_EMAIL_ATTR", "mail"),
        "default_role": os.environ.get("LDAP_DEFAULT_ROLE", "operator"),
    }


class LDAPAuthBackend:
    def is_configured(self) -> bool:
        return bool(os.environ.get("LDAP_HOST", ""))

    def authenticate(self, email: str, password: str) -> dict | None:
        cfg = _ldap_config()
        try:
            server = ldap3.Server(cfg["host"], port=cfg["port"], use_ssl=cfg["use_ssl"])

            service_conn = ldap3.Connection(
                server,
                user=cfg["bind_dn"] or None,
                password=cfg["bind_password"] or None,
                auto_bind=False,
            )
            if cfg["use_starttls"]:
                service_conn.start_tls()
            if not service_conn.bind():
                raise LDAPAuthError(
                    f"Service account bind failed: {service_conn.result.get('description', 'unknown')}"
                )

            search_filter = cfg["user_filter"].format(email=escape_filter_chars(email))
            service_conn.search(
                cfg["base_dn"],
                search_filter,
                ldap3.SUBTREE,
                attributes=[cfg["email_attr"], "displayName", "cn"],
            )
            if not service_conn.entries:
                return None

            entry = service_conn.entries[0]
            user_dn = entry.entry_dn

            user_conn = ldap3.Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=False,
            )
            if not user_conn.bind():
                return None

            email_attr = cfg["email_attr"]
            email_val = str(entry[email_attr]) if email_attr in entry else email
            display_name = (
                str(entry["displayName"]) if "displayName" in entry
                else str(entry["cn"]) if "cn" in entry
                else email
            )
            return {"email": email_val, "display_name": display_name}

        except LDAPException as exc:
            raise LDAPAuthError(str(exc)) from exc
        except LDAPAuthError:
            raise
        except Exception as exc:
            raise LDAPAuthError(str(exc)) from exc
