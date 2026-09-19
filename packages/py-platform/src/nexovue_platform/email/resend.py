"""Resend, behind `EmailSender`.

**The only module that knows a mail provider exists.** The port is next door
and `apps/api/tests/test_provider_ports.py` refuses a vendor's name there;
every piece of Resend-shaped knowledge belongs here and nowhere above.

**Why the HTTP call and not Resend's SDK.** Resend recommends its SDK, and for
most callers that is right — it handles auth, the User-Agent, serialization and
typed errors. This product makes one POST to one endpoint. `store.py` records
the house reasoning for the same trade: an async AWS wrapper *"drags the whole
workspace's AWS SDK backwards and couples the scheduler's STS code to a third
party's release cadence"*. A vendor package for a single call buys convenience
and costs a dependency whose releases we inherit. If a second endpoint is ever
needed — audiences, broadcasts — that arithmetic changes and so should this.

**Templates are in the repository rather than with the provider.** Resend can
host them, and that would put the words a customer reads outside version
control, outside code review and outside `just check`. They are therefore not
Resend-shaped and do not live here either — see `templates.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import httpx

from nexovue_platform.email.sender import Delivery, EmailError, Message
from nexovue_platform.email.settings import EmailSettings
from nexovue_platform.email.templates import render

SEND_ENDPOINT: Final = "https://api.resend.com/emails"

# One send is a single small POST. Generous enough for a slow network, short
# enough that a stalled provider does not hold a request open — a person waiting
# on a signup response should see a failure rather than a spinner.
TIMEOUT_SECONDS: Final = 10.0


class ResendEmailSender:
    """Sends through Resend. Satisfies `EmailSender` structurally, not by
    inheritance — which is what lets a test double exist with no import."""

    def __init__(self, settings: EmailSettings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        # A client may be passed in so a test can drive a transport without a
        # network, and so a caller that already owns a pool can share it.
        self._client = client

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> Delivery:
        subject, body = render(message, data)
        payload = {
            "from": self._settings.from_address,
            "to": [to],
            "reply_to": self._settings.reply_to,
            "subject": subject,
            "text": body,
        }
        headers = {"Authorization": f"Bearer {self._settings.api_key}"}
        if idempotency_key is not None:
            # Remembered by Resend for 24 hours, well past an alert's hour of retries.
            headers["Idempotency-Key"] = idempotency_key

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
