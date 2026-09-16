"""
LDAP authentication utility.

Uses user's own username and password to bind to Active Directory/LDAP.
Optionally enforces membership in a specific AD group DN.
"""

from typing import Optional, Dict
from ldap3 import Server, Connection, ALL, Tls, ALL_ATTRIBUTES, SUBTREE
import ssl
import logging

from config.config import (
    LDAP_SERVER_URI,
    LDAP_USE_SSL,
    LDAP_START_TLS,
    LDAP_BIND_DN_TEMPLATE,
    LDAP_USER_SEARCH_BASE,
    LDAP_USER_FILTER_TEMPLATE,
    LDAP_ALLOWED_GROUP_DN,
    LDAP_ATTRIBUTE_MAIL,
    LDAP_ATTRIBUTE_DISPLAY_NAME,
)

logger = logging.getLogger('text2sql')


class LDAPAuthenticator:
    def __init__(self):
        # Prepare TLS if needed
        self._tls = None
        if LDAP_USE_SSL or LDAP_START_TLS:
            # Default system CA certs
            self._tls = Tls(validate=ssl.CERT_REQUIRED)

    def _build_bind_dn(self, username: str) -> str:
        # Allow DOMAIN\\user or user@domain via template
        return LDAP_BIND_DN_TEMPLATE.format(username=username)

    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        """
        Authenticate against LDAP using provided end-user credentials.
        Returns a dict with basic profile if allowed, otherwise None.
        """
        if not LDAP_SERVER_URI:
            logger.error("LDAP_SERVER_URI is not configured")
            return None

        bind_dn = self._build_bind_dn(username)
        server = Server(LDAP_SERVER_URI, use_ssl=LDAP_USE_SSL, get_info=ALL, tls=self._tls)

        try:
            conn = Connection(server, user=bind_dn, password=password, auto_bind=False)
            if LDAP_START_TLS and not LDAP_USE_SSL:
                conn.open()
                if not conn.start_tls():
                    logger.error("Failed to start TLS on LDAP connection")
                    return None
            if not conn.bind():
                logger.info(f"LDAP bind failed for user {username}")
                return None

            # After successful bind, search the user entry
            if not LDAP_USER_SEARCH_BASE:
                logger.error("LDAP_USER_SEARCH_BASE not configured")
                conn.unbind()
                return None

            user_filter = LDAP_USER_FILTER_TEMPLATE.format(username=username)
            if not conn.search(
                search_base=LDAP_USER_SEARCH_BASE,
                search_filter=user_filter,
                search_scope=SUBTREE,
                attributes=ALL_ATTRIBUTES,
            ):
                logger.info(f"LDAP user not found for {username}")
                conn.unbind()
                return None

            # Use the first entry
            if not conn.entries:
                conn.unbind()
                return None

            entry = conn.entries[0]
            dn = entry.entry_dn

            # Enforce group membership if configured
            if LDAP_ALLOWED_GROUP_DN:
                # Many ADs expose 'memberOf' on the user entry
                member_of = []
                try:
                    member_of = entry.memberOf.values if hasattr(entry, 'memberOf') else []
                except Exception:
                    member_of = []

                if LDAP_ALLOWED_GROUP_DN not in member_of:
                    # Fallback: search group members
                    group_ok = False
                    try:
                        if conn.search(
                            search_base=LDAP_ALLOWED_GROUP_DN,
                            search_filter='(objectClass=group)'.format(),
                            search_scope=SUBTREE,
                            attributes=['member']
                        ):
                            for g in conn.entries:
                                try:
                                    members = g.member.values if hasattr(g, 'member') else []
                                    if dn in members:
                                        group_ok = True
                                        break
                                except Exception:
                                    continue
                    except Exception as ge:
                        logger.warning(f"LDAP group check failed: {ge}")

                    if not group_ok:
                        logger.info(f"LDAP user {username} not in allowed group")
                        conn.unbind()
                        return None

            # Build profile
            email = None
            display_name = None
            try:
                email = getattr(entry, LDAP_ATTRIBUTE_MAIL).value if hasattr(entry, LDAP_ATTRIBUTE_MAIL) else None
            except Exception:
                pass
            try:
                display_name = getattr(entry, LDAP_ATTRIBUTE_DISPLAY_NAME).value if hasattr(entry, LDAP_ATTRIBUTE_DISPLAY_NAME) else None
            except Exception:
                pass

            profile = {
                'dn': dn,
                'username': username,
                'email': email or f"{username}@example.com",
                'display_name': display_name or username,
            }

            conn.unbind()
            return profile

        except Exception as e:
            logger.error(f"LDAP authentication error for {username}: {e}")
            return None
