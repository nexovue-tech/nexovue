"""Mail that is written to the log instead of sent. The development default.

**Why this exists.** Signing up sends a verification message, and nothing about
that is unusual — but it means every account a test creates costs one real
message against a metered allowance. Three signups reach the development API per
`just check`, so a gate people run on every change was spending the product's
mail quota on changes that had nothing to do with mail. One working session
reached 80% of a month's allowance that way.

`require_opt_in` fixed the suite that *meant* to send. This fixes the far larger
number that never mentioned mail at all, and it fixes it where the messages
actually leave: the development API container, which reads the developer's own
credentials from `.env`.

**It renders the real template, through `templates.render`.** A stub that logged
"would have sent VERIFY_EMAIL" would hide exactly the defects this path has —
a caller that forgot `{link}`, a body that reads wrongly once filled. This
composes the message a customer would receive and then declines to post it, so
the only untested step is the HTTP call, which is what
`test_notifications_live.py` is for.

The rendered body is logged in full because a developer needs the link in it:
clicking through a signup is how the verification flow is exercised by hand, and
with no provider configured at all that link goes nowhere a person can reach.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Final

from nexovue_platform.telemetry.logging import get_logger

from nexovue_api.notifications.sender import Delivery, Message
from nexovue_api.notifications.templates import render
from nexovue_api.settings import EmailSettings

logger = get_logger(__name__)

# Marks an id as this adapter's, so one appearing anywhere a real provider's was
# expected — a support lookup, a stored receipt — names its own cause.
MESSAGE_ID_PREFIX: Final = "console-"


class ConsoleEmailSender:
    """Renders the message and logs it. Satisfies `EmailSender` structurally.

    Takes `EmailSettings` like every adapter, so the composition root treats it
    no differently, and reads only `from_address` — there is no credential to
    use and none is required to be valid.
    """

    def __init__(self, settings: EmailSettings) -> None:
        self._settings = settings

    async def send(self, *, to: str, message: Message, data: Mapping[str, Any]) -> Delivery:
        # Rendered before anything is logged: a caller that omitted a field gets
        # the same `EmailError` here as it would in production.
        subject, body = render(message, data)
        logger.info(
            "mail was rendered and not sent: this deployment logs mail instead of sending it",
            mail_to=to,
            mail_from=self._settings.from_address,
            mail_message=message.value,
            mail_subject=subject,
            mail_body=body,
        )
        return Delivery(provider_message_id=f"{MESSAGE_ID_PREFIX}{uuid.uuid4()}")
