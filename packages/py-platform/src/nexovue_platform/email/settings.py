"""What a configured mail sender is, shared by every process that sends mail.

The API sends account mail and the scheduler sends alerts (F21), through one
port and one set of `EMAIL_*` variables. Both read them through `email_settings`,
so the two cannot disagree about whether mail is configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

# Which implementation of the port is used. The vendor's name is the *value*
# here, never the key — `EMAIL_API_KEY` stays neutral so it survives the switch,
# while `EMAIL_PROVIDER` is the thing meant to change. A Literal rather than a
# free string so an unknown value fails when settings are read, at start-up,
# rather than at the first send in front of somebody waiting for a password reset.
#
# `console` is not a vendor: it renders the message and writes it to the log.
# It is what development runs, so a signup in a test does not spend a real
# message — see `console.py`.
EmailProvider = Literal["resend", "console"]

# Who logged mail claims to be from when nothing configured a sender. A
# domain that cannot resolve, so a message that escaped to a real provider
# would be refused rather than delivered from a plausible-looking address.
CONSOLE_FROM_ADDRESS: Final = "nexovue@console.invalid"

# The environment variables an unconfigured email sender tells the caller to
# set. Spelled once so the message and the fields cannot drift apart.
EMAIL_VARIABLES: Final = (
    "EMAIL_PROVIDER",
    "EMAIL_API_KEY",
    "EMAIL_SENDING_DOMAIN",
    "EMAIL_FROM_ADDRESS",
    "EMAIL_REPLY_TO",
)


def refuse_console_mail_in_production(*, env: str, provider: str) -> None:
    """Production may leave mail unconfigured, never pretend to send it.

    `console` reports every message sent and writes it to a log nobody reads, so
    verification, reset, invoice and alert mail would vanish with nothing going
    red (readiness blocker 11). Unconfigured is honest: each sender refuses by
    variable name. Raised as ValueError for the settings validator to name.
    """
    if env == "production" and provider == "console":
        raise ValueError(
            "EMAIL_PROVIDER is 'console', which writes mail to the log and sends none. "
            "Set a real provider, or leave EMAIL_PROVIDER unset to run without mail."
        )


@dataclass(frozen=True, slots=True)
class EmailSettings:
    """A complete email configuration, which is all four values or none.

    Same shape and same reason as `ObjectStoreSettings`: a half-filled
    configuration fails at the first send, and a provider's "domain not
    verified" tells a reader nothing about which variable was missing.

    No provider is named. A vendor's vocabulary stays out of the port,
    and a vendor's name in a settings field is that leak in another file — the
    key's own prefix already identifies which one it is.
    """

    provider: EmailProvider
    api_key: str
    # The domain whose SPF, DKIM and DMARC were verified. `from_address` must be
    # at it; a provider refuses anything else, which is the point of both fields
    # existing rather than one.
    sending_domain: str
    from_address: str
    # A real inbox, deliberately not on the sending domain. Without it a
    # customer who replies to a `noreply` sender is talking to nobody.
    reply_to: str


def email_settings(
    *,
    provider: EmailProvider,
    api_key: str,
    sending_domain: str,
    from_address: str,
    reply_to: str,
) -> EmailSettings | None:
    """The email settings, or None when mail is not configured.

    None rather than an error, because a process that cannot send mail still
    does everything else. The caller that needs to send is the one that must
    refuse, and it can name `EMAIL_VARIABLES` when it does.

    **`console` is configured by naming it, and needs nothing else.** The
    all-or-none rule exists because a half-filled configuration fails at the
    first send with a provider error that names no variable. There is no
    provider to refuse here and no credential to be valid, so the rule has
    nothing to protect — and applying it anyway would mean a clone with no
    `.env` gets no mail at all, when reading the verification link out of the
    log is exactly what development needs.
    """
    if provider == "console":
        return EmailSettings(
            provider=provider,
            api_key="",
            sending_domain=sending_domain,
            from_address=from_address or CONSOLE_FROM_ADDRESS,
            reply_to=reply_to,
        )
    if not all((api_key, sending_domain, from_address, reply_to)):
        return None
    return EmailSettings(
        provider=provider,
        api_key=api_key,
        sending_domain=sending_domain,
        from_address=from_address,
        reply_to=reply_to,
    )
