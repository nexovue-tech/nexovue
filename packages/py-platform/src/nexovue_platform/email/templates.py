"""What a recipient reads. Ours, not a provider's.

`resend.py` records why these live in the repository rather than with the
provider — hosted templates put the words a customer reads *"outside version
control, outside code review and outside `just check`"*. That reasoning makes
them provider-neutral by definition, so they sit here rather than inside any one
adapter, and every adapter renders the same words.

**That sameness is the point, not a tidiness.** A development adapter that
invented its own formatting would accept a caller that forgot `{link}` and let
the missing field surface in production instead — the one place these templates
exist to keep it out of.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from nexovue_platform.email.sender import EmailError, Message

# Subject and a plain-text body per message, filled from the caller's own
# `data`. No HTML: these are one sentence and a link, and an HTML part is a
# second thing to keep in step for no gain a reader would notice.
TEMPLATES: Final[dict[Message, tuple[str, str]]] = {
    Message.VERIFY_EMAIL: (
        "Confirm your email address",
        "Confirm your address to finish setting up nexovue:\n\n{link}\n\n"
        "The link expires in {expires_in}. If you did not sign up, ignore this.",
    ),
    Message.RESET_PASSWORD: (
        "Reset your password",
        "Use this link to set a new password:\n\n{link}\n\n"
        "It expires in {expires_in} and can be used once. If you did not ask for "
        "this, nothing has changed and you can ignore it.",
    ),
    Message.INVOICE: (
        "Your nexovue invoice",
        "Your invoice for {period} is ready: {amount}\n\n{link}",
    ),
    Message.ALERT: (
        "{subject}",
        "{body}\n\nOpen in nexovue: {link}\n\n"
        'You receive this because the alert rule "{rule_name}" notifies this address. '
        "Change or mute it: {manage_link}",
    ),
    Message.CHANNEL_FAILING: (
        "Alerts are not reaching {channel_name}",
        "nexovue has not been able to deliver to the {channel_kind} channel "
        '"{channel_name}" for {failing_for}. The last attempt: {reason}.\n\n'
        "Alerts meant for it are reaching nobody until it is fixed: {link}",
    ),
    Message.WELCOME_OWNER: (
        "Your nexovue organization {organization} is ready",
        "Thank you for choosing nexovue. {organization} is set up and waiting for you.\n\n"
        "Choose your password to sign in:\n\n{link}\n\n"
        "The link expires in {expires_in} and can be used once. If it expires, reply to "
        "this email and we will send a new one.",
    ),
    Message.PAYMENT_FAILED: (
        "Payment for {organization} failed — your plan ends on {until}",
        "We could not collect the payment for {organization}'s {plan} plan, and the "
        "payment provider has stopped retrying.\n\n"
        "The plan stays active until {until}. Update the card or bank account with your "
        "provider, or subscribe again here, before then:\n\n{link}\n\n"
        "If nothing is paid by {until}, {organization} moves to the free plan. "
        "Nothing you have already sent is deleted.",
    ),
    Message.CASE_ASSIGNED: (
        "A case on {service} was assigned to you",
        "You were assigned a case on {service} ({severity}) in nexovue.\n\n"
        "Open it:\n\n{link}\n\n"
        "This email carries no details on purpose. The case page has the evidence "
        "and the notes.",
    ),
    Message.CASE_TRACKER_CLOSED: (
        "The issue for a case on {service} was closed, so the case is resolved",
        "The tracker issue linked to a case on {service} was closed, so nexovue resolved "
        "the case.\n\nIf that was wrong, reopen it in nexovue:\n\n{link}\n\n"
        "This email carries no details on purpose. The case page has the evidence and "
        "the notes.",
    ),
    Message.CASE_TRACKER_CLOSED_SIGNAL_OPEN: (
        "The issue for a case on {service} was closed, and the problem is still measured",
        "The tracker issue linked to a case on {service} was closed, but the signal has not "
        "recovered, so the case stays open in nexovue. It resolves by itself once the signal "
        "recovers, or you can resolve it now:\n\n{link}\n\n"
        "This email carries no details on purpose. The case page has the evidence and "
        "the notes.",
    ),
    Message.CASE_IDLE_WARNING: (
        "A case on {service} ends by itself on {ends_on}",
        "Nobody has acted on a case on {service} in nexovue for a while. It ends by itself "
        "on {ends_on} unless someone adds a note or resolves it:\n\n{link}\n\n"
        "This email carries no details on purpose. The case page has the evidence and "
        "the notes.",
    ),
    Message.CONNECTION_NEEDS_ATTENTION: (
        "{tracker} needs attention in nexovue",
        "nexovue cannot use its {tracker} connection: {what} Issues are not kept in step "
        "until it is fixed. Cases still work.\n\nFix it here:\n\n{link}",
    ),
    Message.CONNECTION_FAILING: (
        "nexovue has not reached {tracker} for a day",
        "nexovue has not reached {tracker} since {failing_since} and keeps trying: {what} "
        "Issues are not kept in step meanwhile. Cases still work.\n\n"
        "See the connection:\n\n{link}",
    ),
    Message.CONNECTION_EXPIRING: (
        "The {tracker} credential nexovue uses expires on {expires_on}",
        "The {tracker} credential nexovue uses expires on {expires_on}. Replace it before "
        "then, or issues stop being kept in step:\n\n{link}",
    ),
    Message.CONNECTION_REPOSITORY_PUBLIC: (
        "{repository} is now public, so nexovue creates no issues there",
        "nexovue saw that {repository} on {tracker} is now public. Anyone can read the "
        "issues already there, including any nexovue created: they were exposed when it "
        "turned public. nexovue refuses new issues there, and syncing an issue there, "
        "unless an admin allows it:\n\n{link}",
    ),
    Message.AI_CONNECTION_CHANGED: (
        "The AI connection for {organization} changed",
        "{what}\n\nAddress before: {old_host}\nAddress now: {new_host}\n\n"
        "AI wording is off until {until}. If you did not expect this, check who changed "
        "it in the audit log:\n\n{link}",
    ),
    Message.INVITE: (
        "{inviter} invited you to {organization} on nexovue",
        "{inviter} invited you to join {organization} on nexovue as {role_phrase}.\n\n"
        "Choose your password to accept:\n\n{link}\n\n"
        "The link expires in {expires_in} and can be used once. If you were not "
        "expecting this, ignore it — nothing happens until the link is used.",
    ),
}


def render(message: Message, data: Mapping[str, Any]) -> tuple[str, str]:
    """Fill a template, and fail loudly on a field the caller did not supply.

    A `KeyError` from `str.format` would name the missing field and nothing
    else. Sending a mail that reads `{link}` to a customer is worse than not
    sending it, so this refuses rather than degrades.
    """
    subject, body = TEMPLATES[message]
    try:
        # Both halves: an alert's subject is the caller's, like its body (F21).
        return subject.format(**data), body.format(**data)
    except KeyError as missing:
        raise EmailError(
            f"{message.value} needs {missing} and the caller did not supply it"
        ) from missing
