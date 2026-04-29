from __future__ import annotations

from copy import deepcopy

def _endpoint_defaults(
    *,
    host: str,
    port: int,
    auth_mode: str,
    oauth_token_url: str,
    oauth_scope: str,
) -> dict:
    return {
        "host": host,
        "port": port,
        "use_ssl": True,
        "tls_profile": "modern",
        "auth_mode": auth_mode,
        "oauth_token_url": oauth_token_url,
        "oauth_scope": oauth_scope,
    }


_PROVIDER_PRESETS: list[dict] = [
    {
        "id": "custom",
        "name": "Custom / Self-hosted",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "pop3": _endpoint_defaults(
                host="",
                port=995,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "caldav": _endpoint_defaults(
                host="",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "carddav": _endpoint_defaults(
                host="",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
        },
        "destination_defaults": {
            "imap": _endpoint_defaults(
                host="",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "caldav": _endpoint_defaults(
                host="",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "carddav": _endpoint_defaults(
                host="",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
        },
        "auth_guidance": {
            "summary": "Use mailbox passwords/app passwords or OAuth/XOAUTH2 tokens, depending on your server policy.",
            "steps": [
                "Confirm IMAP/POP3 is enabled for the mailbox account.",
                "Use app passwords if the provider enforces multi-factor authentication.",
                "Prefer encrypted TLS endpoints (993 for IMAP, 995 for POP3).",
            ],
            "reference_url": "",
        },
    },
    {
        "id": "gmail",
        "name": "Gmail",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="imap.gmail.com",
                port=993,
                auth_mode="password",
                oauth_token_url="https://oauth2.googleapis.com/token",
                oauth_scope="https://mail.google.com/",
            ),
            "pop3": _endpoint_defaults(
                host="pop.gmail.com",
                port=995,
                auth_mode="password",
                oauth_token_url="https://oauth2.googleapis.com/token",
                oauth_scope="https://mail.google.com/",
            ),
            "caldav": _endpoint_defaults(
                host="apidata.googleusercontent.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://oauth2.googleapis.com/token",
                oauth_scope="https://www.googleapis.com/auth/calendar",
            ),
            "carddav": _endpoint_defaults(
                host="www.googleapis.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://oauth2.googleapis.com/token",
                oauth_scope="https://www.googleapis.com/auth/carddav",
            ),
        },
        "destination_defaults": {
            "imap": _endpoint_defaults(
                host="imap.gmail.com",
                port=993,
                auth_mode="password",
                oauth_token_url="https://oauth2.googleapis.com/token",
                oauth_scope="https://mail.google.com/",
            ),
            "caldav": _endpoint_defaults(
                host="apidata.googleusercontent.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://oauth2.googleapis.com/token",
                oauth_scope="https://www.googleapis.com/auth/calendar",
            ),
            "carddav": _endpoint_defaults(
                host="www.googleapis.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://oauth2.googleapis.com/token",
                oauth_scope="https://www.googleapis.com/auth/carddav",
            ),
        },
        "auth_guidance": {
            "summary": "Use app passwords for basic auth or OAuth/XOAUTH2 tokens for modern auth.",
            "steps": [
                "Enable IMAP/POP3 in Gmail settings if source access is required.",
                "Enable 2-Step Verification on the Google account.",
                "Generate an app password and use it instead of your normal login password.",
            ],
            "reference_url": "https://support.google.com/accounts/answer/185833",
        },
    },
    {
        "id": "microsoft365",
        "name": "Microsoft 365 / Outlook",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="outlook.office365.com",
                port=993,
                auth_mode="password",
                oauth_token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                oauth_scope="https://outlook.office.com/.default offline_access",
            ),
            "pop3": _endpoint_defaults(
                host="outlook.office365.com",
                port=995,
                auth_mode="password",
                oauth_token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                oauth_scope="https://outlook.office.com/.default offline_access",
            ),
            "caldav": _endpoint_defaults(
                host="outlook.office365.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                oauth_scope="https://outlook.office.com/.default offline_access",
            ),
            "carddav": _endpoint_defaults(
                host="outlook.office365.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                oauth_scope="https://outlook.office.com/.default offline_access",
            ),
        },
        "destination_defaults": {
            "imap": _endpoint_defaults(
                host="outlook.office365.com",
                port=993,
                auth_mode="password",
                oauth_token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                oauth_scope="https://outlook.office.com/.default offline_access",
            ),
            "caldav": _endpoint_defaults(
                host="outlook.office365.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                oauth_scope="https://outlook.office.com/.default offline_access",
            ),
            "carddav": _endpoint_defaults(
                host="outlook.office365.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                oauth_scope="https://outlook.office.com/.default offline_access",
            ),
        },
        "auth_guidance": {
            "summary": "Tenant policy may block basic auth; OAuth/XOAUTH2 is recommended where possible.",
            "steps": [
                "Verify IMAP/POP3 is enabled for the mailbox in Exchange admin settings.",
                "If MFA is active, use an app password where supported.",
                "Confirm conditional access policies allow legacy protocol access for migration.",
            ],
            "reference_url": "https://learn.microsoft.com/exchange/clients-and-mobile-in-exchange-online/pop3-and-imap4",
        },
    },
    {
        "id": "yahoo",
        "name": "Yahoo Mail",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="imap.mail.yahoo.com",
                port=993,
                auth_mode="password",
                oauth_token_url="https://api.login.yahoo.com/oauth2/get_token",
                oauth_scope="mail-r",
            ),
            "pop3": _endpoint_defaults(
                host="pop.mail.yahoo.com",
                port=995,
                auth_mode="password",
                oauth_token_url="https://api.login.yahoo.com/oauth2/get_token",
                oauth_scope="mail-r",
            ),
            "caldav": _endpoint_defaults(
                host="",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://api.login.yahoo.com/oauth2/get_token",
                oauth_scope="mail-r",
            ),
            "carddav": _endpoint_defaults(
                host="",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://api.login.yahoo.com/oauth2/get_token",
                oauth_scope="mail-r",
            ),
        },
        "destination_defaults": {
            "imap": _endpoint_defaults(
                host="imap.mail.yahoo.com",
                port=993,
                auth_mode="password",
                oauth_token_url="https://api.login.yahoo.com/oauth2/get_token",
                oauth_scope="mail-r",
            ),
            "caldav": _endpoint_defaults(
                host="",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://api.login.yahoo.com/oauth2/get_token",
                oauth_scope="mail-r",
            ),
            "carddav": _endpoint_defaults(
                host="",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://api.login.yahoo.com/oauth2/get_token",
                oauth_scope="mail-r",
            ),
        },
        "auth_guidance": {
            "summary": "Yahoo typically uses app passwords for basic auth; OAuth token mode can also be configured.",
            "steps": [
                "Enable account security settings that allow app-specific passwords.",
                "Generate an app password in Yahoo account security settings.",
                "Use the generated app password in migration source/destination credentials.",
            ],
            "reference_url": "https://help.yahoo.com/kb/SLN15241.html",
        },
    },
    {
        "id": "zoho",
        "name": "Zoho Mail",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="imap.zoho.com",
                port=993,
                auth_mode="password",
                oauth_token_url="https://accounts.zoho.com/oauth/v2/token",
                oauth_scope="ZohoMail.messages.ALL",
            ),
            "pop3": _endpoint_defaults(
                host="pop.zoho.com",
                port=995,
                auth_mode="password",
                oauth_token_url="https://accounts.zoho.com/oauth/v2/token",
                oauth_scope="ZohoMail.messages.ALL",
            ),
            "caldav": _endpoint_defaults(
                host="calendar.zoho.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://accounts.zoho.com/oauth/v2/token",
                oauth_scope="ZohoCalendar.calendar.ALL",
            ),
            "carddav": _endpoint_defaults(
                host="contacts.zoho.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://accounts.zoho.com/oauth/v2/token",
                oauth_scope="ZohoContacts.contactapi.ALL",
            ),
        },
        "destination_defaults": {
            "imap": _endpoint_defaults(
                host="imap.zoho.com",
                port=993,
                auth_mode="password",
                oauth_token_url="https://accounts.zoho.com/oauth/v2/token",
                oauth_scope="ZohoMail.messages.ALL",
            ),
            "caldav": _endpoint_defaults(
                host="calendar.zoho.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://accounts.zoho.com/oauth/v2/token",
                oauth_scope="ZohoCalendar.calendar.ALL",
            ),
            "carddav": _endpoint_defaults(
                host="contacts.zoho.com",
                port=443,
                auth_mode="oauth2",
                oauth_token_url="https://accounts.zoho.com/oauth/v2/token",
                oauth_scope="ZohoContacts.contactapi.ALL",
            ),
        },
        "auth_guidance": {
            "summary": "Use app passwords or OAuth tokens and ensure protocol access is enabled.",
            "steps": [
                "Enable IMAP/POP3 access in Zoho mailbox settings.",
                "Create an app-specific password if multifactor auth is enabled.",
                "Use TLS-enabled ports to avoid authentication failures.",
            ],
            "reference_url": "https://www.zoho.com/mail/help/imap-access.html",
        },
    },
]


def get_provider_presets() -> list[dict]:
    return deepcopy(_PROVIDER_PRESETS)


def get_provider_preset(provider_id: str) -> dict | None:
    provider_id = provider_id.strip().lower()
    for preset in _PROVIDER_PRESETS:
        if str(preset.get("id", "")).lower() == provider_id:
            return deepcopy(preset)
    return None
