"""Resend, behind `EmailSender`.

**The only module in the API that knows a mail provider exists.** ADR 0102 puts
the port next door and `tests/test_provider_ports.py` refuses a vendor's name
there; every piece of Resend-shaped knowledge belongs here and nowhere above.

**Why the HTTP call and not Resend's SDK.** Resend recommends its SDK, and for
most callers that is right — it handles auth, the User-Agent, serialization and
typed errors. This product makes one POST to one endpoint. `store.py` records
the house reasoning for the same trade: an async AWS wrapper *"drags the whole
workspace's AWS SDK backwards and couples the scheduler's STS code to a third
party's release cadence"*. A vendor package for a single call buys convenience
and costs a dependency whose releases we inherit. If a second endpoint is ever
needed — audiences, broadcasts — that arithmetic changes and so should this.

**Templates are here rather than with the provider.** Resend can host them, and
that would put the words a customer reads outside version control, outside code
review and outside `just check`. The bodies below are deliberately plain: a
verification mail is one sentence and a link, and anything more is a marketing
message wearing a transactional message's clothes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import httpx

from nexovue_api.notifications.sender import Delivery, EmailError, Message
from nexovue_api.settings import EmailSettings

SEND_ENDPOINT: Final = "https://api.resend.com/emails"

# One send is a single small POST. Generous enough for a slow network, short
# enough that a stalled provider does not hold a request open — a person waiting
# on a signup response should see a failure rather than a spinner.
TIMEOUT_SECONDS: Final = 10.0

# What a recipient reads. Subject and a plain-text body per message, filled from
# the caller's own `data`. No HTML: these are one sentence and a link, and an
# HTML part is a second thing to keep in step for no gain a reader would notice.
_TEMPLATES: Final[dict[Message, tuple[str, str]]] = {
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
}


class ResendEmailSender:
    """Sends through Resend. Satisfies `EmailSender` structurally, not by
    inheritance — which is what lets a test double exist with no import."""

    def __init__(self, settings: EmailSettings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        # A client may be passed in so a test can drive a transport without a
        # network, and so a caller that already owns a pool can share it.
        self._client = client

    async def send(self, *, to: str, message: Message, data: Mapping[str, Any]) -> Delivery:
        subject, body = _render(message, data)
        payload = {
            "from": self._settings.from_address,
            "to": [to],
            "reply_to": self._settings.reply_to,
            "subject": subject,
            "text": body,
        }
        headers = {"Authorization": f"Bearer {self._settings.api_key}"}

        try:
            if self._client is not None:
                response = await self._client.post(
                    SEND_ENDPOINT, json=payload, headers=headers, timeout=TIMEOUT_SECONDS
                )
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    response = await client.post(SEND_ENDPOINT, json=payload, headers=headers)
        except httpx.HTTPError as failure:
            # The provider was unreachable. Named as such rather than as a
            # refusal, because the two need different responses: one is retried,
            # the other is a configuration error that retrying cannot fix.
            raise EmailError(f"could not reach the mail provider: {failure}") from failure

        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise EmailError(_refusal(response))

        identifier = response.json().get("id", "")
        return Delivery(provider_message_id=str(identifier))


def _render(message: Message, data: Mapping[str, Any]) -> tuple[str, str]:
    """Fill a template, and fail loudly on a field the caller did not supply.

    A `KeyError` from `str.format` would name the missing field and nothing
    else. Sending a mail that reads `{link}` to a customer is worse than not
    sending it, so this refuses rather than degrades.
    """
    subject, body = _TEMPLATES[message]
    try:
        return subject, body.format(**data)
    except KeyError as missing:
        raise EmailError(
            f"{message.value} needs {missing} and the caller did not supply it"
        ) from missing


def _refusal(response: httpx.Response) -> str:
    """The provider's own reason, when it gave one.

    Resend answers a refusal with a JSON body carrying `message`. Passing that
    through matters more than it looks: "domain is not verified" and "invalid
    API key" are the two failures that actually happen, and a bare status code
    tells whoever is reading the log neither.
    """
    try:
        detail = response.json().get("message") or response.text
    except ValueError:
        detail = response.text
    return f"the mail provider refused the message ({response.status_code}): {detail}"
