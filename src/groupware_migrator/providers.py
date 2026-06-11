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
    # ── German / DACH providers ────────────────────────────────────────────
    {
        "id": "gmx",
        "name": "GMX Mail (gmx.de / gmx.net)",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="imap.gmx.net",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "pop3": _endpoint_defaults(
                host="pop.gmx.net",
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
                host="imap.gmx.net",
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
            "summary": "GMX requires IMAP to be enabled in account settings; use an app password if 2-factor auth is active.",
            "steps": [
                "Log in to GMX and open E-Mail → E-Mail abrufen (POP3 & IMAP).",
                "Activate 'Extern auf meine GMX E-Mails zugreifen' to allow IMAP/POP3.",
                "If 2-Step-Verification is enabled, create an app password under Sicherheit → App-Passwörter.",
            ],
            "reference_url": "https://hilfe.gmx.net/pop-imap/imap/imap-serverdaten.html",
        },
    },
    {
        "id": "webde",
        "name": "WEB.DE",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="imap.web.de",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "pop3": _endpoint_defaults(
                host="pop.web.de",
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
                host="imap.web.de",
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
            "summary": "WEB.DE requires external access to be explicitly enabled; use an app password when 2FA is active.",
            "steps": [
                "Log in and navigate to E-Mail → E-Mail am PC empfangen.",
                "Enable 'Externen Zugriff und Apps erlauben' for IMAP/POP3 access.",
                "Create an app password under Mein Account → Sicherheit if 2FA is enabled.",
            ],
            "reference_url": "https://hilfe.web.de/pop-imap/imap/imap-serverdaten.html",
        },
    },
    {
        "id": "tonline",
        "name": "T-Online (Telekom)",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="secureimap.t-online.de",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "pop3": _endpoint_defaults(
                host="securepop.t-online.de",
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
                host="secureimap.t-online.de",
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
            "summary": "T-Online uses a separate 'E-Mail Passwort' (not your Telekom login) for IMAP access.",
            "steps": [
                "Log in to mein.telekom.de and navigate to E-Mail → E-Mail Passwort.",
                "Set or reset the E-Mail Passwort — this is separate from your Telekom account password.",
                "Use your full @t-online.de address as the IMAP username.",
            ],
            "reference_url": "https://www.telekom.de/hilfe/e-mail/e-mail-programm/server-einstellungen",
        },
    },
    {
        "id": "posteo",
        "name": "Posteo",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="posteo.de",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "pop3": _endpoint_defaults(
                host="posteo.de",
                port=995,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "caldav": _endpoint_defaults(
                host="posteo.de",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "carddav": _endpoint_defaults(
                host="posteo.de",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
        },
        "destination_defaults": {
            "imap": _endpoint_defaults(
                host="posteo.de",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "caldav": _endpoint_defaults(
                host="posteo.de",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "carddav": _endpoint_defaults(
                host="posteo.de",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
        },
        "auth_guidance": {
            "summary": "Posteo supports IMAP, CalDAV, and CardDAV with standard password authentication; no app passwords needed.",
            "steps": [
                "Use your full Posteo address and account password for all protocols.",
                "CalDAV calendar path: /calendars/<username>/default/",
                "CardDAV address book path: /addressbooks/<username>/default/",
            ],
            "reference_url": "https://posteo.de/en/help/what-are-posteos-imap-pop3-and-smtp-server-settings",
        },
    },
    {
        "id": "mailboxorg",
        "name": "mailbox.org",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="imap.mailbox.org",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "pop3": _endpoint_defaults(
                host="pop3.mailbox.org",
                port=995,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "caldav": _endpoint_defaults(
                host="dav.mailbox.org",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "carddav": _endpoint_defaults(
                host="dav.mailbox.org",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
        },
        "destination_defaults": {
            "imap": _endpoint_defaults(
                host="imap.mailbox.org",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "caldav": _endpoint_defaults(
                host="dav.mailbox.org",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "carddav": _endpoint_defaults(
                host="dav.mailbox.org",
                port=443,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
        },
        "auth_guidance": {
            "summary": "mailbox.org supports IMAP, CalDAV, and CardDAV; create an app password if 2FA is active.",
            "steps": [
                "Use your full mailbox.org address and account password.",
                "For 2FA accounts, generate an app password under Einstellungen → Sicherheit → App-Passwörter.",
                "DAV endpoint for both CalDAV and CardDAV: dav.mailbox.org",
            ],
            "reference_url": "https://kb.mailbox.org/en/private/e-mail-article/imap-and-smtp-server-information",
        },
    },
    {
        "id": "ionos",
        "name": "IONOS / 1&1 Mail",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="imap.ionos.de",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "pop3": _endpoint_defaults(
                host="pop.ionos.de",
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
                host="imap.ionos.de",
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
            "summary": "IONOS supports IMAP with standard password auth; no special activation is required.",
            "steps": [
                "Use your full IONOS email address and account password.",
                "If your account was provisioned at 1und1.de, use imap.1und1.de and pop.1und1.de instead.",
                "App passwords are not required unless 2FA is enabled in the IONOS control panel.",
            ],
            "reference_url": "https://www.ionos.de/hilfe/e-mail/posteingangsserver-pop3-und-imap/imap-posteingangsserver/",
        },
    },
    {
        "id": "strato",
        "name": "Strato Mail",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="imap.strato.de",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "pop3": _endpoint_defaults(
                host="pop3.strato.de",
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
                host="imap.strato.de",
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
            "summary": "Strato uses standard IMAP with the account password; no extra activation needed.",
            "steps": [
                "Use your full Strato email address and account password.",
                "Verify IMAP access is included in your Strato hosting package.",
                "SMTP outbound: smtp.strato.de:465 (SSL) or smtp.strato.de:587 (STARTTLS).",
            ],
            "reference_url": "https://www.strato.de/faq/mail/die-strato-posteingangsserver/",
        },
    },
    {
        "id": "freenet",
        "name": "Freenet Mail (freenet.de)",
        "source_defaults": {
            "imap": _endpoint_defaults(
                host="imap.freenet.de",
                port=993,
                auth_mode="password",
                oauth_token_url="",
                oauth_scope="",
            ),
            "pop3": _endpoint_defaults(
                host="pop.freenet.de",
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
                host="imap.freenet.de",
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
            "summary": "Freenet Mail uses standard password authentication for IMAP and POP3 access.",
            "steps": [
                "Use your full freenet.de address and account password.",
                "Enable external mail client access in Freenet webmail settings if prompted.",
                "SMTP outbound: smtp.freenet.de:587 (STARTTLS).",
            ],
            "reference_url": "https://email.freenet.de/hilfe/imap",
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


# ---------------------------------------------------------------------------
# IdP presets for OIDC/SSO configuration
# ---------------------------------------------------------------------------

def get_idp_presets() -> list[dict]:
    from groupware_migrator.engine.oidc import IDP_PRESETS
    return deepcopy(IDP_PRESETS)
