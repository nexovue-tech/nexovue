"""Transactional mail: the port, its implementations, and the one place that
chooses between them.

Shared because two processes send mail: the API (verification, password reset,
invoices) and the scheduler (alerts, F21). One port, one set of templates, one
`EMAIL_*` configuration, so the two cannot drift.

**`build_email_sender` is the composition root, and it is the third part of the
pattern.** A port and an adapter alone still make a caller name a provider at
construction; this is what keeps that name in one file. Every caller asks for
`EmailSender` and never learns what answered.

Which provider is used comes from `EMAIL_PROVIDER` in the environment, so
switching one is configuration rather than a code change. The vendor's name is
the *value* — `EMAIL_API_KEY` stays neutral and survives the switch.
"""

from __future__ import annotations

from nexovue_platform.email.sender import Delivery, EmailError, EmailSender, Message
from nexovue_platform.email.settings import (
    EMAIL_VARIABLES,
    EmailProvider,
    EmailSettings,
    email_settings,
)

__all__ = [
    "EMAIL_VARIABLES",
    "Delivery",
    "EmailError",
    "EmailSender",
    "EmailSettings",
    "Message",
    "build_email_sender",
    "email_settings",
]


def build_email_sender(settings: EmailSettings) -> EmailSender:
    """The one place a mail provider is chosen.

    A match rather than a registry dict: `EmailProvider` is a `Literal`, so mypy
    fails the day a provider is added to it and not handled here. A dict keyed by
    string would accept the new name and raise at runtime instead.
    """
    provider: EmailProvider = settings.provider
    match provider:
        case "resend":
            # Imported here rather than at module scope so adding a provider
            # does not make every caller import every vendor's client.
            from nexovue_platform.email.resend import ResendEmailSender

            return ResendEmailSender(settings)
        case "console":
            from nexovue_platform.email.console import ConsoleEmailSender

            return ConsoleEmailSender(settings)
